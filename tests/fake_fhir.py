"""Server FHIR in memoria, montato come adapter di requests, per i test offline.

Implementa solo il sottoinsieme usato dal progetto (conditional update, transaction,
search con _tag/_id/_lastUpdated/_include/paginazione). Ogni risorsa ricevuta viene
validata con i modelli Pydantic di fhir.resources (R4B, strutturalmente allineato a R4
per le risorse usate qui): una risorsa non conforme fa fallire il test.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from fhir.resources.R4B import get_fhir_model_class
from requests.adapters import BaseAdapter

BASE = "http://fake-fhir.local/baseR4"


class FakeFhirServer(BaseAdapter):
    def __init__(self, page_size: int = 3):
        super().__init__()
        self.store: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.seq = 0
        self.clock = datetime(2026, 10, 13, 8, 0, 0, tzinfo=timezone.utc)
        self.page_size = page_size
        self.requests: List[str] = []

    # ------------------------------------------------------------------ helpers
    def _tick(self) -> str:
        self.clock += timedelta(seconds=1)
        return self.clock.isoformat().replace("+00:00", "Z")

    def _validate(self, res: Dict[str, Any]) -> None:
        get_fhir_model_class(res["resourceType"]).model_validate(res)

    def _save(self, res: Dict[str, Any], rid: str | None = None, ts: str | None = None) -> Dict[str, Any]:
        self._validate(res)
        rtype = res["resourceType"]
        if rid is None:
            self.seq += 1
            rid = str(self.seq)
        res = copy.deepcopy(res)
        res["id"] = rid
        res.setdefault("meta", {})
        res["meta"]["lastUpdated"] = ts or self._tick()
        self.store.setdefault(rtype, {})[rid] = res
        return res

    @staticmethod
    def _response(req, status: int, body: Dict[str, Any]) -> requests.Response:
        resp = requests.Response()
        resp.status_code = status
        resp._content = json.dumps(body).encode()
        resp.headers["Content-Type"] = "application/fhir+json"
        resp.url = req.url
        resp.request = req
        return resp

    # ------------------------------------------------------------- dispatching
    def send(self, request, **kwargs):  # noqa: D401
        url = urlparse(request.url)
        path = url.path[len(urlparse(BASE).path):].strip("/")
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.requests.append(f"{request.method} {path}?{url.query}")
        body = json.loads(request.body) if request.body else None
        try:
            if request.method == "GET" and path == "metadata":
                return self._response(request, 200, {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1",
                                                     "software": {"name": "FakeFhirServer"}})
            if request.method == "PUT" and "/" not in path:
                return self._conditional_update(request, path, q, body)
            if request.method == "POST" and path == "":
                return self._transaction(request, body)
            if request.method == "GET" and "/" not in path:
                return self._search(request, path, q)
        except Exception as exc:  # errore di validazione -> 400 OperationOutcome
            return self._response(request, 400, {"resourceType": "OperationOutcome",
                                                 "issue": [{"severity": "error", "code": "invalid",
                                                            "diagnostics": str(exc)[:2000]}]})
        return self._response(request, 404, {"resourceType": "OperationOutcome", "issue": []})

    def close(self):
        pass

    # ------------------------------------------------------------- operations
    def _conditional_update(self, req, rtype, q, body):
        system, value = q["identifier"].split("|", 1)
        matches = [r for r in self.store.get(rtype, {}).values()
                   if any(i.get("system") == system and i.get("value") == value for i in r.get("identifier", []))]
        if "_tag" in q:
            tsys, tcode = q["_tag"].split("|", 1)
            matches = [r for r in matches if any(t.get("system") == tsys and t.get("code") == tcode
                                                 for t in r.get("meta", {}).get("tag", []))]
        if len(matches) > 1:
            return self._response(req, 412, {"resourceType": "OperationOutcome", "issue": []})
        rid = matches[0]["id"] if matches else None
        saved = self._save(body, rid)
        return self._response(req, 200 if rid else 201, saved)

    def _transaction(self, req, bundle):
        assert bundle["type"] == "transaction"
        ts = self._tick()  # stessa lastUpdated per tutta la transazione (come HAPI)
        mapping: Dict[str, str] = {}
        for e in bundle["entry"]:
            self.seq += 1
            mapping[e["fullUrl"]] = f"{e['resource']['resourceType']}/{self.seq}"

        def fix(node):
            if isinstance(node, dict):
                return {k: (mapping.get(v, v) if k == "reference" else fix(v)) for k, v in node.items()}
            if isinstance(node, list):
                return [fix(x) for x in node]
            return node

        out = []
        for e in bundle["entry"]:
            res = fix(e["resource"])
            rid = mapping[e["fullUrl"]].split("/")[1]
            self._save(res, rid, ts)
            out.append({"response": {"status": "201 Created", "location": f"{mapping[e['fullUrl']]}/_history/1"}})
        return self._response(req, 200, {"resourceType": "Bundle", "type": "transaction-response", "entry": out})

    def _search(self, req, rtype, q):
        res = list(self.store.get(rtype, {}).values())
        if "_tag" in q:
            system, code = q["_tag"].split("|", 1)
            res = [r for r in res if any(t.get("system") == system and t.get("code") == code
                                         for t in r.get("meta", {}).get("tag", []))]
        if "_id" in q:
            ids = set(q["_id"].split(","))
            res = [r for r in res if r["id"] in ids]
        if q.get("_lastUpdated", "").startswith("gt"):
            res = [r for r in res if r["meta"]["lastUpdated"] > q["_lastUpdated"][2:]]
        res.sort(key=lambda r: (r["meta"]["lastUpdated"], int(r["id"])))
        offset = int(q.get("_getpagesoffset", "0"))
        size = min(int(q.get("_count", "20")), self.page_size)
        page = res[offset:offset + size]
        entries = [{"resource": r, "search": {"mode": "match"}} for r in page]
        if q.get("_include") == "ChargeItem:service":
            seen = set()
            for r in page:
                for s in r.get("service", []):
                    pid = s["reference"].split("/")[-1]
                    if pid in self.store.get("Procedure", {}) and pid not in seen:
                        seen.add(pid)
                        entries.append({"resource": self.store["Procedure"][pid], "search": {"mode": "include"}})
        links = []
        if offset + size < len(res):
            nq = dict(q, _getpagesoffset=str(offset + size))
            links.append({"relation": "next", "url": f"{BASE}/{rtype}?{urlencode(nq)}"})
        return self._response(req, 200, {"resourceType": "Bundle", "type": "searchset", "total": len(res),
                                         "link": links, "entry": entries})


def make_session(server: FakeFhirServer) -> requests.Session:
    s = requests.Session()
    s.mount("http://fake-fhir.local", server)
    return s
