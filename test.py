import argparse
import csv
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag


DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
FLIGHT_RE = re.compile(r"\b[A-Z0-9]{2}\s?\d{1,5}\b")

URL_LIKE_RE = re.compile(
    r"""["']((?:https?:)?//[^"' ]+|/[^"' ]+|[A-Za-z0-9_\-./]+(?:ajax|api|flight)[A-Za-z0-9_\-./?=&%]*)["']""",
    re.I,
)

ACTION_RE = re.compile(r"""action\s*[:=]\s*["']([a-zA-Z0-9_\-]+)["']""", re.I)
NONCE_RE = re.compile(r"""nonce\s*[:=]\s*["']([a-zA-Z0-9_\-]{6,})["']""", re.I)


def clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def looks_like_date(s: str) -> bool:
    return bool(DATE_RE.search(s or ""))


def extract_flight_code(s: str) -> Optional[str]:
    m = FLIGHT_RE.search(s or "")
    if not m:
        return None
    return m.group(0).replace(" ", "")


def abs_url(base: str, maybe_rel: Optional[str]) -> Optional[str]:
    if not maybe_rel:
        return None
    if maybe_rel.startswith("//"):
        return "https:" + maybe_rel
    if maybe_rel.startswith("http://") or maybe_rel.startswith("https://"):
        return maybe_rel
    return urljoin(base, maybe_rel)


def parse_rows_from_html(html: str, base_url: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select(".flight-result__row")

    out: List[Dict[str, Any]] = []
    for row in rows:
        # дата — ближайший предыдущий .flight-result__head, который реально похож на dd.mm.yyyy
        date = None
        h = row.find_previous(class_="flight-result__head")
        while h:
            ht = clean(h.get_text())
            if looks_like_date(ht):
                date = ht
                break
            h = h.find_previous(class_="flight-result__head")

        status_el = row.select_one(".flight-status span")
        status = clean(status_el.get_text()) if status_el else None

        time_spans = [clean(s.get_text()) for s in row.select(".col-time span")]
        time_1 = time_spans[0] if len(time_spans) > 0 else None
        time_2 = time_spans[1] if len(time_spans) > 1 else None

        city_el = row.select_one(".flight-name")
        city = clean(city_el.get_text()) if city_el else None

        flight_code_block = row.select_one(".flight-code")
        flight_code_raw = clean(flight_code_block.get_text(" ", strip=True)) if flight_code_block else ""
        flight_code = extract_flight_code(flight_code_raw)

        airline_el = row.select_one(".flight-info .desc")
        airline = clean(airline_el.get_text()) if airline_el else None

        terminal_el = row.select_one(".col-terminal .terminal span")
        terminal = clean(terminal_el.get_text()) if terminal_el else None

        logo_el = row.select_one(".col-time img")
        logo_url = abs_url(base_url, logo_el.get("src")) if logo_el else None

        item = {
            "date": date,
            "status": status,
            "time_1": time_1,
            "time_2": time_2,
            "city": city,
            "flight_code": flight_code,
            "flight_code_raw": flight_code_raw or None,
            "airline": airline,
            "terminal": terminal,
            "logo_url": logo_url,
        }

        # отбрасываем совсем пустые строки
        if not (item["flight_code"] or item["city"] or item["airline"]):
            continue

        out.append(item)

    return out


def try_parse_json(text: str) -> Optional[Any]:
    t = (text or "").strip()
    if not t:
        return None
    if not (t.startswith("{") or t.startswith("[")):
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


def extract_candidate_urls_from_html(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    candidates: List[str] = []

    # 1) script src
    for s in soup.find_all("script", src=True):
        u = abs_url(base_url, s.get("src"))
        if u:
            candidates.append(u)

    # 2) inline scripts
    for s in soup.find_all("script"):
        txt = s.get_text() or ""
        for m in URL_LIKE_RE.finditer(txt):
            u = m.group(1)
            u2 = abs_url(base_url, u) if (u.startswith("/") or u.startswith("//") or u.startswith("http")) else abs_url(base_url, u)
            if u2:
                candidates.append(u2)

    # 3) data-* attributes with url/endpoint/ajax
    for tag in soup.find_all(True):
        for attr, val in (tag.attrs or {}).items():
            if not isinstance(val, str):
                continue
            a = attr.lower()
            if ("url" in a or "endpoint" in a or "ajax" in a) and val:
                u = abs_url(base_url, val)
                if u:
                    candidates.append(u)

    # фильтр: оставим похожее на endpoint (ajax/api/flight) и выкинем картинки/шрифты
    out = []
    for u in candidates:
        ul = u.lower()
        if any(ul.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".svg", ".woff", ".woff2", ".ttf"]):
            continue
        if ("ajax" in ul) or ("api" in ul) or ("flight" in ul) or ("arrival" in ul) or ("departure" in ul):
            out.append(u)

    # уникализация с сохранением порядка
    uniq = []
    seen = set()
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def extract_actions_and_nonce(text: str) -> Tuple[List[str], List[str]]:
    actions = list({m.group(1) for m in ACTION_RE.finditer(text or "")})
    nonces = list({m.group(1) for m in NONCE_RE.finditer(text or "")})
    return actions, nonces


@dataclass
class EndpointHit:
    url: str
    method: str
    kind: str  # "html" or "json"
    extra: Dict[str, Any]


class FlightScraper:
    def __init__(self, base_url: str, headers: Dict[str, str], debug: bool = False):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.headers = headers
        self.debug = debug

    def get(self, url: str, **kwargs) -> requests.Response:
        h = dict(self.headers)
        h.update(kwargs.pop("headers", {}))
        resp = self.session.get(url, headers=h, timeout=30, **kwargs)
        return resp

    def post(self, url: str, data: Dict[str, Any], **kwargs) -> requests.Response:
        h = dict(self.headers)
        h.update(kwargs.pop("headers", {}))
        resp = self.session.post(url, headers=h, data=data, timeout=30, **kwargs)
        return resp

    def find_working_endpoint(self, shell_url: str, params: Dict[str, Any]) -> EndpointHit:
        r = self.get(shell_url, params=params)
        r.raise_for_status()
        html = r.text

        # если вдруг уже всё в html — отлично
        if "flight-result__row" in html:
            return EndpointHit(url=shell_url, method="GET", kind="html", extra={"params": params})

        candidates = extract_candidate_urls_from_html(html, shell_url)
        # также скачиваем js-кандидаты и вытягиваем из них ссылки на ajax endpoint’ы
        expanded: List[str] = []
        actions: List[str] = []
        nonces: List[str] = []

        for u in candidates[:30]:
            # берём только js/css? чаще endpoint прячется в js
            if not u.lower().endswith(".js"):
                continue
            try:
                rr = self.get(u)
                if rr.status_code != 200:
                    continue
                txt = rr.text
                a, n = extract_actions_and_nonce(txt)
                actions += a
                nonces += n
                for m in URL_LIKE_RE.finditer(txt):
                    u2 = m.group(1)
                    u3 = abs_url(shell_url, u2) if (u2.startswith("/") or u2.startswith("//") or u2.startswith("http")) else abs_url(shell_url, u2)
                    if u3:
                        expanded.append(u3)
            except Exception:
                continue

        # общий пул endpoint-кандидатов: из HTML + из JS
        pool = candidates + expanded

        # приоритет: admin-ajax.php / ajax / api / flights
        def score(u: str) -> int:
            ul = u.lower()
            s = 0
            if "admin-ajax.php" in ul:
                s += 50
            if "ajax" in ul:
                s += 30
            if "api" in ul:
                s += 20
            if "flight" in ul:
                s += 20
            if ul.endswith(".js"):
                s -= 10
            return s

        pool = sorted(list(dict.fromkeys(pool)), key=score, reverse=True)

        if self.debug:
            print("[debug] candidate pool size:", len(pool))
            print("[debug] top candidates:", pool[:10])
            print("[debug] actions found:", actions[:10])
            print("[debug] nonces found:", nonces[:3])

        # пробуем endpoints
        for u in pool[:60]:
            ul = u.lower()
            if ul.endswith(".js") or ul.endswith(".css"):
                continue

            # 1) пробуем GET с теми же params
            try:
                rr = self.get(u, params=params)
                if rr.status_code == 200:
                    if "flight-result__row" in rr.text:
                        if self.debug:
                            print("[debug] hit HTML endpoint via GET:", u)
                        return EndpointHit(url=u, method="GET", kind="html", extra={"params": params})

                    j = try_parse_json(rr.text)
                    if j is not None:
                        if self.debug:
                            print("[debug] hit JSON endpoint via GET:", u)
                        return EndpointHit(url=u, method="GET", kind="json", extra={"params": params, "json": j})
            except Exception:
                pass

            # 2) если похоже на ajax — пробуем POST (WordPress/admin-ajax и т.п.)
            if ("ajax" in ul) or ul.endswith(".php"):
                base_data = dict(params)

                # если нашли action/nonce — попробуем несколько комбинаций
                action_candidates = actions[:5] or ["get_flights", "flights", "load_flights", "arrival", "departure"]
                nonce_candidates = nonces[:2]

                tried = 0
                for act in action_candidates:
                    data = dict(base_data)
                    data["action"] = act
                    for nn in nonce_candidates:
                        data["nonce"] = nn
                        try:
                            rr = self.post(u, data=data)
                            tried += 1
                            if rr.status_code != 200:
                                continue
                            if "flight-result__row" in rr.text:
                                if self.debug:
                                    print("[debug] hit HTML endpoint via POST:", u, "action=", act)
                                return EndpointHit(url=u, method="POST", kind="html", extra={"data": data})
                            j = try_parse_json(rr.text)
                            if j is not None:
                                if self.debug:
                                    print("[debug] hit JSON endpoint via POST:", u, "action=", act)
                                return EndpointHit(url=u, method="POST", kind="json", extra={"data": data, "json": j})
                        except Exception:
                            continue
                    if tried > 8:
                        break

        raise RuntimeError(
            "Не удалось найти endpoint с рейсами через HTML/JS скан. "
            "В 99% случаев это значит: endpoint требует специфичный параметр/nonce или идёт с нестандартным URL. "
            "Тогда проще всего: DevTools -> Network -> XHR -> Copy as cURL и перенести в requests."
        )

    def fetch_all_pages_html(self, hit: EndpointHit, max_pages: int = 50) -> str:
        """
        Возвращает суммарный HTML, склеенный из всех страниц/порций, если пагинация есть.
        """
        chunks: List[str] = []

        def request_with(extra_params: Dict[str, Any]) -> Optional[str]:
            if hit.method == "GET":
                p = dict(hit.extra.get("params", {}))
                p.update(extra_params)
                rr = self.get(hit.url, params=p)
            else:
                d = dict(hit.extra.get("data", {}))
                d.update(extra_params)
                rr = self.post(hit.url, data=d)
            if rr.status_code != 200:
                return None
            return rr.text

        # 1) базовый ответ
        base = request_with({})
        if not base:
            return ""
        chunks.append(base)

        # если уже много строк — всё, но всё равно попробуем пагинацию (на случай порций)
        # 2) пробуем разные схемы пагинации
        pagers = [
            ("page", 2, 1),      # page=2..N
            ("paged", 2, 1),
            ("p", 2, 1),
            ("offset", 20, 20),  # offset=20,40,...
            ("start", 20, 20),
        ]

        base_rows = base.count("flight-result__row")
        if self.debug:
            print("[debug] base rows in endpoint response:", base_rows)

        # Попробуем найти первую работающую схему, где количество строк меняется
        chosen = None
        for name, start, step in pagers:
            test = request_with({name: start})
            if not test:
                continue
            test_rows = test.count("flight-result__row")
            # если ответ пустой или такой же как base — не наш pager
            if test_rows == 0:
                continue
            if test.strip() == base.strip():
                continue
            chosen = (name, start, step)
            chunks.append(test)
            if self.debug:
                print(f"[debug] pagination detected: {name} starting at {start} step {step}")
            break

        if not chosen:
            # вероятно, всё пришло одним куском
            return "\n".join(chunks)

        name, start, step = chosen
        current = start + step
        stable = 0
        prev_sig = None

        for _ in range(max_pages - 2):
            text = request_with({name: current})
            if not text:
                break
            rows_cnt = text.count("flight-result__row")
            if rows_cnt == 0:
                break

            # сигнатура, чтобы не зациклиться на одинаковых страницах
            sig = (rows_cnt, hash(text[:2000]))
            if sig == prev_sig:
                stable += 1
            else:
                stable = 0
            prev_sig = sig

            # если несколько раз подряд одинаковое — стоп
            if stable >= 2:
                break

            chunks.append(text)
            current += step

        return "\n".join(chunks)


def save_csv(path: str, items: List[Dict[str, Any]]) -> None:
    keys: List[str] = []
    seen = set()
    for it in items:
        for k in it.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="arrival", choices=["arrival", "departure"])
    ap.add_argument("--airport", default="TAS")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--out-json", default="flights.json")
    ap.add_argument("--out-csv", default="")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    shell_url = f"https://www.tashkent-airport.uz/flights/{args.status}"
    params = {"status": args.status, "airport": args.airport}

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": shell_url,
    }

    scraper = FlightScraper(base_url="https://www.tashkent-airport.uz", headers=headers, debug=args.debug)

    hit = scraper.find_working_endpoint(shell_url, params=params)
    if args.debug:
        print("[debug] using endpoint:", hit.method, hit.url, hit.kind)

    if hit.kind == "json":
        # если вдруг нашли JSON — просто сохраняем как есть
        data = hit.extra.get("json")
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("Saved JSON (raw).")
        return

    # HTML endpoint: вытаскиваем все порции/страницы и парсим
    big_html = scraper.fetch_all_pages_html(hit, max_pages=args.max_pages)

    items = parse_rows_from_html(big_html, base_url=shell_url)

    # дедупликация
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for it in items:
        key = (it.get("date"), it.get("flight_code"), it.get("time_1"), it.get("city"), it.get("terminal"))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(it)

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    if args.out_csv:
        save_csv(args.out_csv, cleaned)

    print("total parsed:", len(cleaned))
    if cleaned:
        print("sample:", cleaned[0])


if __name__ == "__main__":
    main()
