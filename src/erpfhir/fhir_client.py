"""Client FHIR R4 minimale basato su requests (REST + JSON)."""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

import requests

FHIR_JSON = "application/fhir+json"


class FhirError(RuntimeError):
    """Errore restituito dal server FHIR (con il testo dell'OperationOutcome)."""


def _outcome_text(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    if body.get("resourceType") == "OperationOutcome":
        return "; ".join(
            f"{i.get('severity')}: {i.get('diagnostics') or i.get('details', {}).get('text', '')}"
            for i in body.get("issue", [])
        )
    return str(body)[:500]


class FhirClient:
    def __init__(self, base_url: str, timeout: int = 30, session: Optional[requests.Session] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": FHIR_JSON, "Content-Type": FHIR_JSON})

    # ------------------------------------------------------------------ helpers
    def _check(self, resp: requests.Response) -> Dict[str, Any]:
        if resp.status_code >= 400:
            raise FhirError(f"HTTP {resp.status_code} su {resp.request.method} {resp.url}: {_outcome_text(resp)}")
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------- interactions
    def capability(self) -> Dict[str, Any]:
        return self._check(self.session.get(f"{self.base_url}/metadata", params={"_summary": "true"},
                                            timeout=self.timeout))

    def conditional_update(self, resource: Dict[str, Any], identifier_system: str, identifier_value: str,
                           extra_params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """PUT [type]?identifier=system|value[&...]  -> crea o aggiorna senza duplicati (idempotente)."""
        rtype = resource["resourceType"]
        params = {"identifier": f"{identifier_system}|{identifier_value}", **(extra_params or {})}
        resp = self.session.put(
            f"{self.base_url}/{rtype}",
            params=params,
            json=resource,
            headers={"Prefer": "return=representation"},
            timeout=self.timeout,
        )
        return self._check(resp)

    def transaction(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        """POST di un Bundle di tipo transaction: tutto o niente."""
        return self._check(self.session.post(self.base_url, json=bundle, timeout=self.timeout))

    def read(self, rtype: str, rid: str) -> Dict[str, Any]:
        return self._check(self.session.get(f"{self.base_url}/{rtype}/{rid}", timeout=self.timeout))

    def search(self, rtype: str, params: Dict[str, str]) -> Iterator[Dict[str, Any]]:
        """Ricerca con paginazione: restituisce le entry di tutte le pagine (match + include)."""
        url: Optional[str] = f"{self.base_url}/{rtype}"
        query: Optional[Dict[str, str]] = params
        while url:
            bundle = self._check(self.session.get(url, params=query, timeout=self.timeout))
            for entry in bundle.get("entry", []):
                yield entry
            url = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
            query = None  # il link "next" contiene gia' tutti i parametri

    def search_resources(self, rtype: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        return [e["resource"] for e in self.search(rtype, params) if "resource" in e]
