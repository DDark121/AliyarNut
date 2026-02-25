import uuid
from datetime import datetime
from typing import Any, Dict
from zoneinfo import ZoneInfo

from icecream import ic
from langchain_core.messages import HumanMessage, SystemMessage

from ai_service.agents.core_agent import agent
from ai_service.memory.database import delete_thread_data
from ai_service.services.reminder_scheduler import reminder_scheduler


class ChatService:
    TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

    @staticmethod
    def _short_text(value: Any, limit: int = 900) -> str:
        compact = " ".join(str(value or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."

    @classmethod
    def _log_rag_usage(cls, result: Dict[str, Any], current_human_content: str) -> None:
        all_messages = list(result.get("messages") or [])
        messages = all_messages
        for idx in range(len(all_messages) - 1, -1, -1):
            msg = all_messages[idx]
            msg_type = str(getattr(msg, "type", "") or "")
            msg_content = str(getattr(msg, "content", "") or "")
            if msg_type == "human" and msg_content == current_human_content:
                messages = all_messages[idx + 1 :]
                break

        rag_called = False
        rag_tool_calls = 0
        rag_outputs: list[str] = []

        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None)
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if isinstance(call, dict):
                        call_name = str(call.get("name", "") or "")
                    else:
                        call_name = str(getattr(call, "name", "") or "")
                    if call_name == "search_docs_knowledge":
                        rag_called = True
                        rag_tool_calls += 1

            msg_type = str(getattr(msg, "type", "") or "")
            msg_name = str(getattr(msg, "name", "") or "")
            if not msg_name:
                msg_name = str(getattr(msg, "additional_kwargs", {}).get("name", "") or "")
            if msg_type == "tool" and msg_name == "search_docs_knowledge":
                rag_called = True
                rag_outputs.append(cls._short_text(getattr(msg, "content", "")))

        ic(f"RAG CALLED: {rag_called}; tool_calls={rag_tool_calls}; outputs={len(rag_outputs)}")
        for idx, output in enumerate(rag_outputs, start=1):
            ic(f"RAG OUTPUT #{idx}: {output}")

    async def process_message(
        self,
        message: str,
        thread_id: str | None,
        context: Dict[str, Any],
    ) -> tuple[str, str]:
        final_thread_id = thread_id or str(uuid.uuid4())

        context_data = {**context}
        context_data.setdefault("user_name", "guest")
        now_tashkent = datetime.now(self.TASHKENT_TZ)
        context_data["current_datetime"] = now_tashkent.strftime("%Y-%m-%d %H:%M:%S")
        context_data["current_date"] = now_tashkent.strftime("%Y-%m-%d")
        context_data["current_time"] = now_tashkent.strftime("%H:%M:%S")
        context_data["current_timezone"] = "Asia/Tashkent"

        user_message = str(message)
        try:
            await reminder_scheduler.on_client_message(
                thread_id=final_thread_id,
                context=context_data,
                message=user_message,
            )
        except Exception as exc:
            ic(f"on_client_message failed for thread={final_thread_id}: {exc}")

        system_message = SystemMessage(
            content=(
                "Служебная информация о текущем времени: "
                f"{context_data['current_datetime']} "
                f"({context_data['current_timezone']}). "
                "Считай это текущим временем в диалоге."
            )
        )
        human_message = HumanMessage(content=user_message)
        config = {"configurable": {"thread_id": final_thread_id, "context": context_data}}

        ic("➡️ AGENT INPUT:", [system_message, human_message], context_data)

        result = await agent.ainvoke(
            input={"messages": [system_message, human_message]},
            context=context_data,
            config=config,
        )
        self._log_rag_usage(result, human_message.content)

        response_text = result["messages"][-1].content
        return final_thread_id, str(response_text)

    async def delete_history(self, thread_id: str) -> bool:
        """Удаляет историю диалога"""
        return await delete_thread_data(thread_id)


chat_service = ChatService()
