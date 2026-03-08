from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from icecream import ic
from langchain_community.document_loaders import Docx2txtLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


class RagKnowledgeError(Exception):
    pass


class RagKnowledgeService:
    def __init__(self) -> None:
        default_docs = "/app/data/docs" if Path("/app").exists() else "data/docs"
        default_index = "/app/data/rag_faiss" if Path("/app").exists() else "data/rag_faiss"

        self.docs_dir = Path(os.getenv("RAG_DOCS_DIR", default_docs))
        self.index_dir = Path(os.getenv("RAG_INDEX_DIR", default_index))
        self.embedding_model_name = os.getenv(
            "RAG_EMBEDDING_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ).strip()
        self.embedding_device = (os.getenv("RAG_EMBEDDING_DEVICE", "cpu").strip() or "cpu").lower()
        self.chunk_size = int(os.getenv("RAG_CHUNK_SIZE", "1000"))
        self.chunk_overlap = int(os.getenv("RAG_CHUNK_OVERLAP", "200"))
        self.search_k = int(os.getenv("RAG_SEARCH_K", "4"))

        self._lock = asyncio.Lock()
        self._vectorstore: FAISS | None = None
        self._embeddings: HuggingFaceEmbeddings | None = None

    @property
    def _manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    def _ensure_embeddings_sync(self) -> HuggingFaceEmbeddings:
        if self._embeddings is None:
            self._embeddings = HuggingFaceEmbeddings(
                model_name=self.embedding_model_name,
                model_kwargs={"device": self.embedding_device},
            )
        return self._embeddings

    def _iter_doc_files_sync(self) -> list[Path]:
        if not self.docs_dir.exists() or not self.docs_dir.is_dir():
            raise RagKnowledgeError("База знаний пуста: папка документов не найдена.")

        supported = {".md", ".markdown", ".txt", ".docx"}
        files = [
            path
            for path in self.docs_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in supported
        ]
        if not files:
            raise RagKnowledgeError("База знаний пуста: добавьте документы в data/docs.")
        return sorted(files, key=lambda p: str(p).lower())

    def _build_manifest_sync(self, files: list[Path]) -> dict[str, Any]:
        file_items: list[dict[str, Any]] = []
        for path in files:
            st = path.stat()
            try:
                rel = path.relative_to(self.docs_dir)
                rel_str = rel.as_posix()
            except ValueError:
                rel_str = str(path)
            file_items.append(
                {
                    "path": rel_str,
                    "size": int(st.st_size),
                    "mtime_ns": int(st.st_mtime_ns),
                }
            )

        return {
            "embedding_model": self.embedding_model_name,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "files": file_items,
        }

    def _load_manifest_sync(self) -> dict[str, Any] | None:
        if not self._manifest_path.exists():
            return None
        try:
            with self._manifest_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            ic(f"rag manifest read failed: {exc}")
            return None
        return payload if isinstance(payload, dict) else None

    def _save_manifest_sync(self, manifest: dict[str, Any]) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        with self._manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

    @staticmethod
    def _manifest_equal(left: dict[str, Any] | None, right: dict[str, Any]) -> bool:
        if not left:
            return False
        return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
            right, sort_keys=True, ensure_ascii=False
        )

    def _has_saved_index_sync(self) -> bool:
        return (self.index_dir / "index.faiss").exists() and (self.index_dir / "index.pkl").exists()

    def _load_file_documents_sync(self, file_path: Path) -> list[Any]:
        suffix = file_path.suffix.lower()
        if suffix == ".docx":
            loader = Docx2txtLoader(str(file_path))
        else:
            loader = TextLoader(str(file_path), encoding="utf-8", autodetect_encoding=True)

        docs = loader.load()
        for doc in docs:
            metadata = dict(getattr(doc, "metadata", {}) or {})
            metadata["source"] = str(file_path)
            doc.metadata = metadata
        return docs

    def _build_index_sync(self, files: list[Path], manifest: dict[str, Any]) -> tuple[int, int]:
        all_docs: list[Any] = []
        for path in files:
            try:
                all_docs.extend(self._load_file_documents_sync(path))
            except Exception as exc:
                ic(f"rag loader failed for {path}: {exc}")

        if not all_docs:
            raise RagKnowledgeError("Не удалось загрузить ни одного документа для RAG.")

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        chunks = splitter.split_documents(all_docs)
        if not chunks:
            raise RagKnowledgeError("Не удалось разбить документы на чанки для RAG.")

        embeddings = self._ensure_embeddings_sync()
        vectorstore = FAISS.from_documents(chunks, embeddings)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        vectorstore.save_local(str(self.index_dir))
        self._save_manifest_sync(manifest)
        self._vectorstore = vectorstore
        return len(all_docs), len(chunks)

    def _ensure_index_sync(self, *, force_rebuild: bool = False) -> dict[str, Any]:
        files = self._iter_doc_files_sync()
        current_manifest = self._build_manifest_sync(files)

        has_index = self._has_saved_index_sync()
        saved_manifest = self._load_manifest_sync()
        should_rebuild = force_rebuild or (not has_index) or (not self._manifest_equal(saved_manifest, current_manifest))

        if should_rebuild:
            docs_count, chunks_count = self._build_index_sync(files, current_manifest)
            return {
                "rebuilt": True,
                "docs_count": docs_count,
                "chunks_count": chunks_count,
                "files_count": len(files),
            }

        if self._vectorstore is None:
            embeddings = self._ensure_embeddings_sync()
            self._vectorstore = FAISS.load_local(
                str(self.index_dir),
                embeddings,
                allow_dangerous_deserialization=True,
            )
        return {
            "rebuilt": False,
            "docs_count": None,
            "chunks_count": None,
            "files_count": len(files),
        }

    def _search_sync(self, query: str, top_k: int) -> list[Any]:
        if self._vectorstore is None:
            raise RagKnowledgeError("RAG индекс не загружен.")
        retriever = self._vectorstore.as_retriever(search_kwargs={"k": max(1, int(top_k))})
        return retriever.invoke(query)

    @staticmethod
    def _format_snippet(value: str, max_len: int = 500) -> str:
        compact = " ".join(str(value or "").split())
        if len(compact) <= max_len:
            return compact
        return compact[: max_len - 3] + "..."

    @staticmethod
    def _format_source(path_raw: Any) -> str:
        path = Path(str(path_raw or "").strip())
        if not str(path):
            return "unknown"
        return path.name or str(path)

    async def search(self, *, query: str, top_k: int | None = None) -> str:
        q = str(query or "").strip()
        if not q:
            raise ValueError("Передайте текст запроса для поиска по базе знаний.")

        k = max(1, int(top_k or self.search_k))
        async with self._lock:
            state = await asyncio.to_thread(self._ensure_index_sync, force_rebuild=False)
            hits = await asyncio.to_thread(self._search_sync, q, k)

        if not hits:
            return "По базе знаний совпадений не найдено."

        lines: list[str] = []
        if state.get("rebuilt"):
            lines.append(
                "Индекс базы знаний обновлен автоматически "
                f"(файлов: {state.get('files_count')}, чанков: {state.get('chunks_count')})."
            )
        lines.append(f"Найдено фрагментов: {len(hits)}.")

        for idx, doc in enumerate(hits, start=1):
            meta = dict(getattr(doc, "metadata", {}) or {})
            source = self._format_source(meta.get("source"))
            snippet = self._format_snippet(getattr(doc, "page_content", ""))
            lines.append(f"{idx}. [{source}] {snippet}")

        return "\n".join(lines)

    async def ensure_index(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._ensure_index_sync, force_rebuild=False)

    async def rebuild(self) -> str:
        async with self._lock:
            state = await asyncio.to_thread(self._ensure_index_sync, force_rebuild=True)
        return (
            "Индекс базы знаний пересобран. "
            f"Файлов: {state.get('files_count')}, чанков: {state.get('chunks_count')}."
        )


rag_knowledge_service = RagKnowledgeService()
