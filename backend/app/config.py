"""Runtime configuration (environment variables only; no secrets in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ANALYSIS_VERSION = "astra-1.0.0"
SERVICE_NAME = "astra-backend"


def _default_data_dir() -> Path:
    # astra/backend/app/config.py -> astra/data
    return Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    etherscan_api_key: str | None = field(default=None, repr=False)  # never printed
    etherscan_base_url: str = "https://api.etherscan.io/v2/api"
    provider_timeout_s: float = 10.0
    provider_max_retries: int = 3
    provider_backoff_s: float = 0.5
    cache_ttl_s: int = 300  # 0 disables caching
    supported_chains: tuple[int, ...] = (1,)  # Ethereum mainnet only (blueprint: Ethereum-first)
    max_wallet_transfers: int = 500
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173",
                                     "http://localhost:4173", "http://127.0.0.1:4173")
    osint_github_enabled: bool = False
    github_token: str | None = field(default=None, repr=False)  # never printed
    max_investigations: int = 200
    evaluation_seeds: tuple[int, ...] = (1, 2, 3, 4, 5)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        chains = tuple(int(c) for c in e.get("ASTRA_SUPPORTED_CHAINS", "1").split(",") if c.strip())
        return cls(
            data_dir=Path(e["ASTRA_DATA_DIR"]) if e.get("ASTRA_DATA_DIR") else _default_data_dir(),
            etherscan_api_key=e.get("ETHERSCAN_API_KEY") or None,
            etherscan_base_url=e.get("ETHERSCAN_BASE_URL", "https://api.etherscan.io/v2/api"),
            provider_timeout_s=float(e.get("ASTRA_PROVIDER_TIMEOUT_S", "10")),
            provider_max_retries=int(e.get("ASTRA_PROVIDER_MAX_RETRIES", "3")),
            provider_backoff_s=float(e.get("ASTRA_PROVIDER_BACKOFF_S", "0.5")),
            cache_ttl_s=int(e.get("ASTRA_CACHE_TTL_SECONDS", "300")),
            supported_chains=chains or (1,),
            max_wallet_transfers=int(e.get("ASTRA_MAX_WALLET_TRANSFERS", "500")),
            cors_origins=tuple(o.strip() for o in e.get("ASTRA_CORS_ORIGINS", ",".join(cls.cors_origins)).split(",")
                               if o.strip() and o.strip() != "*"),
            osint_github_enabled=e.get("ASTRA_OSINT_GITHUB_ENABLED", "0") == "1",
            github_token=e.get("GITHUB_TOKEN") or None,
            max_investigations=int(e.get("ASTRA_MAX_INVESTIGATIONS", "200")),
            evaluation_seeds=tuple(int(x) for x in e.get("ASTRA_EVALUATION_SEEDS", "1,2,3,4,5").split(",") if x.strip()),
        )
