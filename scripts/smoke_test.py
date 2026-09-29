"""Smoke test de ponta a ponta da API (usado no CI e após o deploy no CD).

Pressupõe que o pipeline já rodou (clientes do Golden Set materializados no Redis).
Uso: python scripts/smoke_test.py [--url http://localhost:8000] [--wait 120] [--no-feedback]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def wait_healthy(base_url: str, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if call(base_url, "GET", "/health")[0] == 200:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(3)
    sys.exit(f"API não ficou saudável em {timeout}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--wait", type=int, default=120, help="segundos aguardando /health")
    parser.add_argument(
        "--no-feedback", action="store_true", help="não envia feedback real (não altera os contadores do bandit)"
    )
    args = parser.parse_args()
    base = args.url.rstrip("/")

    wait_healthy(base, args.wait)
    failures = []

    def check(name: str, condition: bool, detail: object = "") -> None:
        print(f"[{'OK' if condition else 'FALHOU'}] {name} {detail}")
        if not condition:
            failures.append(name)

    status, rec = call(base, "POST", "/recommend", {"client_id": 900003, "campaign_month": "may"})
    check(
        "POST /recommend (golden 900003, maio)",
        status == 200 and rec.get("context") == "senior|may",
        f"-> {status} {rec.get('recommended_offer')}",
    )

    if status == 200:
        rec_id = rec["recommendation_id"]
        # Validações que não gravam nada: seguras também em produção.
        status, _ = call(base, "POST", "/feedback", {"recommendation_id": rec_id, "reward": 5})
        check("POST /feedback com reward inválido retorna 422", status == 422, f"-> {status}")
        status, _ = call(base, "POST", "/feedback", {"recommendation_id": 2**62, "reward": 1})
        check("POST /feedback de recomendação inexistente retorna 404", status == 404, f"-> {status}")

        if not args.no_feedback:
            status, fb = call(base, "POST", "/feedback", {"recommendation_id": rec_id, "reward": 1})
            check("POST /feedback", status == 200 and fb.get("reward") == 1, f"-> {status}")
            status, _ = call(base, "POST", "/feedback", {"recommendation_id": rec_id, "reward": 1})
            check("POST /feedback repetido retorna 409", status == 409, f"-> {status}")

    status, _ = call(base, "POST", "/recommend", {"client_id": 123456789})
    check("POST /recommend de cliente inexistente retorna 404", status == 404, f"-> {status}")

    status, arms = call(base, "GET", "/arms?month=may")
    check(
        "GET /arms?month=may", status == 200 and set(arms) == {"jovem|may", "adulto|may", "senior|may"}, f"-> {status}"
    )

    if failures:
        sys.exit(f"{len(failures)} verificação(ões) falharam: {failures}")
    print("Smoke test OK")


if __name__ == "__main__":
    main()
