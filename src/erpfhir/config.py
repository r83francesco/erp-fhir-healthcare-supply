"""Configurazione letta da variabili d'ambiente (vedi .env.example)."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Sistemi di identificazione (NamingSystem) usati nelle risorse FHIR.
# Il dominio erp.example.org e' volutamente fittizio.
SID_ERP_ITEM = "http://erp.example.org/fhir/sid/item-number"
SID_ERP_SUBINV = "http://erp.example.org/fhir/sid/subinventory"
SID_DEMO_CHARGE = "http://erp.example.org/fhir/sid/consumption-id"
SID_GTIN = "https://www.gs1.org/gtin"
UDI_ISSUER_GS1 = "http://hl7.org/fhir/NamingSystem/gs1-di"
TAG_SYSTEM = "http://erp.example.org/fhir/CodeSystem/demo-run"


def _local_demo_tag() -> str:
    """Tag univoco per questa copia del progetto, generato al primo avvio e salvato in .demo_tag:
    evita che chi clona il repository legga i dati di altri sul server pubblico."""
    path = PROJECT_ROOT / ".demo_tag"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    tag = f"erp-fhir-demo-{uuid.uuid4().hex[:8]}"
    path.write_text(tag, encoding="utf-8")
    return tag


@dataclass
class Settings:
    fhir_base_url: str = field(
        default_factory=lambda: os.getenv("FHIR_BASE_URL", "https://hapi.fhir.org/baseR4").rstrip("/")
    )
    # Etichetta (meta.tag) che isola i NOSTRI dati sul server pubblico condiviso.
    demo_tag: str = field(default_factory=lambda: os.getenv("DEMO_TAG") or _local_demo_tag())
    db_backend: str = field(default_factory=lambda: os.getenv("DB_BACKEND", "sqlite").lower())
    sqlite_path: str = field(
        default_factory=lambda: os.getenv("SQLITE_PATH", str(PROJECT_ROOT / "erp_demo.db"))
    )
    oracle_user: str = field(default_factory=lambda: os.getenv("ORACLE_USER", ""))
    oracle_password: str = field(default_factory=lambda: os.getenv("ORACLE_PASSWORD", ""))
    oracle_dsn: str = field(default_factory=lambda: os.getenv("ORACLE_DSN", "localhost:1521/FREEPDB1"))
    organization_id: int = field(default_factory=lambda: int(os.getenv("ORGANIZATION_ID", "101")))
    http_timeout: int = field(default_factory=lambda: int(os.getenv("HTTP_TIMEOUT", "30")))
