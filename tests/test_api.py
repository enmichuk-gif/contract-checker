"""End-to-end тесты HTTP API: анализ, вопросы, статика, ошибки.

Сервер поднимается в отдельном потоке на случайном порту (порт 0), запросы —
через urllib. Это же сценарий, которым пользуется веб-интерфейс.
"""
import base64
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import server as srv
from tests.synthetic import load_samples, make_contract


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, resp.read()


class TestApi(ApiTestBase):
    def _analyze_payload(self, files=None, as_of="2026-03-10"):
        files = files if files is not None else load_samples()
        return {
            "as_of": as_of,
            "files": [{"name": n, "content_b64": base64.b64encode(raw).decode()}
                      for n, raw in files],
        }

    def test_analyze_samples(self):
        status, data = self.post("/api/analyze", self._analyze_payload())
        self.assertEqual(status, 200)
        self.assertEqual(len(data["contracts"]), 3)
        self.assertIn("session_id", data)
        c1 = next(c for c in data["contracts"] if c["number"] == "STN-101")
        self.assertEqual(c1["fields"]["amount"]["value"], 1_380_000)
        self.assertEqual(c1["fields"]["delivery_date"]["value"], "2026-04-30")
        c3 = next(c for c in data["contracts"] if c["number"] == "STN-103")
        self.assertEqual(c3["fields"]["amount"]["status"], "no_data")
        # тексты документов возвращаются для просмотрщика цитат
        self.assertIn("01_contract.txt", data["document_texts"])

    def test_analyze_plain_utf8_content(self):
        payload = {"as_of": "2026-03-10",
                   "files": [{"name": "c.txt", "content": make_contract("P-1", amount="55 000")}]}
        status, data = self.post("/api/analyze", payload)
        self.assertEqual(status, 200)
        self.assertEqual(data["contracts"][0]["fields"]["amount"]["value"], 55_000)

    def test_question_flow(self):
        _, data = self.post("/api/analyze", self._analyze_payload())
        sid = data["session_id"]
        # q1: срок поставки
        status, ans = self.post("/api/question",
                                {"session_id": sid, "question_id": "q1",
                                 "params": {"contract": "STN-101"}})
        self.assertEqual(status, 200)
        self.assertEqual(ans["value"], "2026-04-30")
        # q2: сумма договора
        status, ans = self.post("/api/question",
                                {"session_id": sid, "question_id": "q2",
                                 "params": {"contract": "STN-102"}})
        self.assertEqual(status, 200)
        self.assertEqual(ans["value"], 840_000)
        # q3: поставка раньше даты
        status, ans = self.post("/api/question",
                                {"session_id": sid, "question_id": "q3",
                                 "params": {"date": "2026-04-01"}})
        self.assertEqual(status, 200)
        self.assertEqual([m["contract_no"] for m in ans["matched"]], ["STN-102"])

    def test_question_with_bad_session(self):
        status, data = self.post("/api/question",
                                 {"session_id": "nope", "question_id": "q1", "params": {}})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_analyze_without_files(self):
        status, data = self.post("/api/analyze", {"as_of": "2026-03-10", "files": []})
        self.assertEqual(status, 400)

    def test_static_pages(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Проверка условий договоров".encode("utf-8"), body)
        status, body = self.get("/app.js")
        self.assertEqual(status, 200)
        status, body = self.get("/style.css")
        self.assertEqual(status, 200)

    def test_sample_documents_endpoint(self):
        status, body = self.get("/api/sample-documents")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        names = [d["name"] for d in data["documents"]]
        self.assertEqual(len(names), 6)
        self.assertIn("01_contract.txt", names)

    def test_directory_traversal_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../run.py")
        self.assertIn(ctx.exception.code, (400, 403, 404))


if __name__ == "__main__":
    unittest.main()
