"""Exchange an Authentik machine identity for octo-sts GitHub tokens and store them in a Secret."""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"token-minter: {name} is not set")
    return value


def request(url: str, *, data: bytes | None = None, headers: dict[str, str], method: str = "GET", context=None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30, context=context) as res:
            return json.load(res)
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")[:300]
        host = urllib.parse.urlsplit(url).netloc
        sys.exit(f"token-minter: {method} {host} returned HTTP {err.code}: {body}")


def authentik_token() -> str:
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": env("AUTHENTIK_CLIENT_ID"),
            "username": env("OCTO_STS_IDENTITY"),
            "password": env("AUTHENTIK_APP_PASSWORD"),
            "scope": "openid profile",
        }
    ).encode()
    body = request(
        env("AUTHENTIK_TOKEN_URL"),
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    token = body.get("access_token")
    if not token:
        sys.exit("token-minter: Authentik response has no access_token")
    return token


def octo_sts_token(bearer: str, owner: str) -> str:
    query = urllib.parse.urlencode({"scope": owner, "identity": env("OCTO_STS_IDENTITY")})
    body = request(
        f"{env('OCTO_STS_URL').rstrip('/')}/sts/exchange?{query}",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    token = body.get("token")
    if not token:
        sys.exit(f"token-minter: octo-sts returned no token for {owner}")
    return token


def write_secret(namespace: str, name: str, data: dict[str, str]) -> None:
    with open(f"{SA_DIR}/token") as fh:
        sa_token = fh.read().strip()
    context = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    patch = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace},
        "type": "Opaque",
        "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()},
    }
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    url = f"https://{host}:{port}/api/v1/namespaces/{namespace}/secrets/{name}?fieldManager=token-minter&force=true"
    request(
        url,
        data=json.dumps(patch).encode(),
        headers={"Authorization": f"Bearer {sa_token}", "Content-Type": "application/apply-patch+yaml"},
        method="PATCH",
        context=context,
    )


def main() -> None:
    # TOKENS="code=routivo,knowledge=lkshrk": Secret key -> GitHub owner whose trust policy applies.
    pairs = [item.split("=", 1) for item in env("TOKENS").split(",") if item.strip()]
    bearer = authentik_token()
    tokens = {key.strip(): octo_sts_token(bearer, owner.strip()) for key, owner in pairs}
    namespaces = [ns.strip() for ns in env("SECRET_NAMESPACES").split(",") if ns.strip()]
    for namespace in namespaces:
        write_secret(namespace, env("SECRET_NAME"), tokens)
    print(f"token-minter: refreshed {', '.join(sorted(tokens))} in {', '.join(namespaces)}")


if __name__ == "__main__":
    main()
