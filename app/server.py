"""HTTP-сервер прототипа на стандартной библиотеке Python (без зависимостей).

Маршруты:
  GET  /                     — веб-интерфейс (папка web/);
  GET  /api/sample-documents — учебные документы из папки documents/ (демо-загрузка);
  POST /api/analyze          — анализ загруженных TXT: {as_of, files:[{name, content_b64}]};
  POST /api/question         — ответ на вопрос: {session_id, question_id, params}.

Сессии анализа хранятся в памяти процесса (прототип; ограничение описано в README).
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import mimetypes
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import questions as q
from . import resolution
from . import textutils as tu

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")
DOCUMENTS_DIR = os.path.join(BASE_DIR, "documents")
MAX_BODY = 50 * 1024 * 1024  # 50 МБ на запрос

# session_id -> (AnalysisResult, as_of_iso). Храним живые объекты: движок
# вопросов работает с ними напрямую (а не с JSON), что упрощает код и тесты.
SESSIONS: dict[str, tuple] = {}


def _read_sample_documents() -> list[dict]:
    out = []
    if not os.path.isdir(DOCUMENTS_DIR):
        return out
    for name in sorted(os.listdir(DOCUMENTS_DIR)):
        if not name.lower().endswith(".txt"):
            continue
        path = os.path.join(DOCUMENTS_DIR, name)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        text, _enc = tu.decode_bytes(raw)
        out.append({"name": name, "content": text})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "ContractChecker/0.1"

    # --- helpers -------------------------------------------------------------
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Optional[dict]:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _send_file(self, path: str) -> None:
        if not os.path.isfile(path):
            self._send_json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Перенаправляем стандартный лог http.server в модуль logging."""
        log.debug("HTTP: %s", fmt % args)

    # --- routes ----------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        t0 = time.monotonic()
        if path in ("/", "/index.html"):
            self._send_file(os.path.join(WEB_DIR, "index.html"))
            log.info("GET %s -> index.html (%.1fms)", path, (time.monotonic() - t0) * 1000)
            return
        if path == "/favicon.ico":
            # Иконки нет; отдаём 204 вместо 404, чтобы не засорять консоль браузера.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/sample-documents":
            self._send_json({"documents": _read_sample_documents()})
            log.info("GET %s -> sample documents (%.1fms)", path, (time.monotonic() - t0) * 1000)
            return
        if path in ("/app.js", "/style.css"):
            self._send_file(os.path.join(WEB_DIR, path.lstrip("/")))
            log.debug("GET %s -> static file (%.1fms)", path, (time.monotonic() - t0) * 1000)
            return
        if path.startswith("/api/"):
            self._send_json({"error": "unknown endpoint"}, 404)
            log.warning("GET %s -> 404 unknown API endpoint", path)
            return
        # защита от выхода за пределы web/
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith("..") or os.path.isabs(rel):
            self._send_json({"error": "forbidden"}, 403)
            log.warning("GET %s -> 403 forbidden (path traversal attempt)", path)
            return
        self._send_file(os.path.join(WEB_DIR, rel))
        log.debug("GET %s -> static file (%.1fms)", path, (time.monotonic() - t0) * 1000)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/analyze":
            return self._handle_analyze()
        if path == "/api/question":
            return self._handle_question()
        self._send_json({"error": "unknown endpoint"}, 404)
        log.warning("POST %s -> 404 unknown endpoint", path)

    # --- handlers ----------------------------------------------------------------
    def _handle_analyze(self):
        t0 = time.monotonic()
        payload = self._read_json()
        if payload is None:
            log.warning("POST /api/analyze -> 400: некорректный JSON или слишком большой запрос")
            return self._send_json({"error": "Некорректный JSON или слишком большой запрос."}, 400)
        files_in = payload.get("files") or []
        as_of = tu.parse_any_date(payload.get("as_of"))
        log.info("POST /api/analyze: получено %d файл(ов), as_of=%s",
                 len(files_in), as_of or "не указана")
        files: list[tuple[str, bytes]] = []
        for i, item in enumerate(files_in):
            name = str(item.get("name") or f"file_{i+1}.txt")
            if item.get("content_b64"):
                try:
                    raw = base64.b64decode(item["content_b64"])
                except Exception:
                    log.error("POST /api/analyze: файл «%s» — некорректный base64", name)
                    return self._send_json({"error": f"Файл «{name}»: некорректный base64."}, 400)
            elif item.get("content") is not None:
                raw = str(item["content"]).encode("utf-8")
            else:
                raw = b""
            files.append((name, raw))
        if not files:
            log.warning("POST /api/analyze -> 400: не передано ни одного файла")
            return self._send_json({"error": "Не передано ни одного файла."}, 400)
        try:
            result = resolution.analyze(files, as_of=as_of)
        except Exception as exc:  # прототип: любая ошибка ядра видна пользователю
            log.exception("POST /api/analyze: ошибка анализа — %s", exc)
            return self._send_json({"error": f"Ошибка анализа: {exc}"}, 500)
        result.questions_meta = q.build_questions_meta(result)
        session_id = uuid.uuid4().hex
        SESSIONS[session_id] = (result, result.as_of)
        elapsed = (time.monotonic() - t0) * 1000
        log.info(
            "POST /api/analyze: завершено за %.1fms — %d договор(ов), %d документ(ов), "
            "сессия %s",
            elapsed, len(result.contracts), len(result.documents), session_id[:12],
        )
        data = result.to_dict()
        data["session_id"] = session_id
        return self._send_json(data)

    def _handle_question(self):
        t0 = time.monotonic()
        payload = self._read_json()
        if payload is None:
            log.warning("POST /api/question -> 400: некорректный JSON")
            return self._send_json({"error": "Некорректный JSON."}, 400)
        session_id = payload.get("session_id")
        if session_id not in SESSIONS:
            log.warning("POST /api/question -> 400: сессия не найдена (id=%s)",
                        str(session_id)[:12] if session_id else "None")
            return self._send_json(
                {"error": "Сессия анализа не найдена — загрузите документы повторно."}, 400)
        result, _as_of = SESSIONS[session_id]
        question_id = str(payload.get("question_id") or "")
        params = payload.get("params") or {}
        log.info("POST /api/question: question_id=%s, params=%s, session=%s",
                 question_id, params, str(session_id)[:12])
        answer = q.answer_question(result, question_id, params)
        elapsed = (time.monotonic() - t0) * 1000
        log.info("POST /api/question: ответ status=%s за %.1fms",
                 answer.get("status", "?"), elapsed)
        return self._send_json(answer)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Прототип проверки условий договоров поставки (Semantic TextNavigator)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="адрес интерфейса (по умолчанию 127.0.0.1; 0.0.0.0 — для сети)")
    parser.add_argument("--port", type=int, default=8000, help="порт (по умолчанию 8000)")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="уровень логирования (по умолчанию INFO)")
    parser.add_argument(
        "--log-file", default=None,
        help="путь к файлу журнала (по умолчанию — вывод в stderr)")
    args = parser.parse_args(argv)

    # Настройка логирования: единый формат, уровень из CLI, опционально — файл.
    log_format = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
    log_datefmt = "%Y-%m-%d %H:%M:%S"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format=log_format,
        datefmt=log_datefmt,
        handlers=handlers,
    )

    log.info("Запуск сервера: http://%s:%d/  (логирование: %s)", args.host, args.port, args.log_level)
    if args.log_file:
        log.info("Файл журнала: %s", args.log_file)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Остановлено пользователем (KeyboardInterrupt).")
        server.server_close()


if __name__ == "__main__":
    main()
