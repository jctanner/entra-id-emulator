# Configuring OpenDataHub Gateway with entra-id-emulator BYOIDC

This documents the additional configuration needed to integrate OpenDataHub's GatewayConfig with the entra-id-emulator when the cluster is already running in OIDC mode.

## Prerequisites

- OCP 4.21 cluster already configured for BYOIDC (see [ocp-byoidc-config.md](ocp-byoidc-config.md))
- OpenDataHub deployed with a GatewayConfig CR (`default-gateway`)
- Gateway route at `rh-ai.apps.ocp.lab.net`

## Step 1: Create a dedicated OIDC client in the emulator

Create a confidential client for the ODH gateway with the appropriate redirect URI.

```bash
ENTRA=https://entra.ocp.lab.net:8443
TENANT=a1b2c3d4-e5f6-7890-abcd-ef1234567890

curl -sk -X POST "$ENTRA/admin/api/clients" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "33333333-4444-5555-6666-777777777777",
    "tenant_id": "'"$TENANT"'",
    "display_name": "OpenDataHub Gateway",
    "client_secret": "odh-client-secret",
    "client_type": "confidential",
    "redirect_uris": ["https://rh-ai.apps.ocp.lab.net/oauth2/callback"],
    "allowed_scopes": ["openid", "profile", "email", "offline_access"]
  }'
```

## Step 2: Create the client secret in OpenShift

The GatewayConfig expects the client secret in the `openshift-ingress` namespace.

```bash
oc create secret generic odh-oidc-client-secret \
    --from-literal=clientSecret=odh-client-secret \
    -n openshift-ingress
```

## Step 3: Add the client ID to the Authentication CR audiences

The kube-apiserver must recognize the new client ID as a valid audience, otherwise tokens issued for this client will be rejected with 401. This is an additive change — existing audiences are not modified.

```bash
oc patch authentication cluster --type=json \
  -p '[{"op": "add", "path": "/spec/oidcProviders/0/issuer/audiences/-", "value": "33333333-4444-5555-6666-777777777777"}]'
```

Verify all three audiences are present:

```bash
oc get authentication cluster -o jsonpath='{.spec.oidcProviders[0].issuer.audiences}'
# ["11111111-2222-3333-4444-555555555555","22222222-3333-4444-5555-666666666666","33333333-4444-5555-6666-777777777777"]
```

The kube-apiserver will restart to pick up the change. Wait for it to settle:

```bash
watch oc get co kube-apiserver
```

## Step 4: Patch the GatewayConfig with OIDC settings

```bash
oc patch gatewayconfig default-gateway --type=merge -p '{
  "spec": {
    "verifyProviderCertificate": false,
    "oidc": {
      "clientID": "33333333-4444-5555-6666-777777777777",
      "issuerURL": "https://entra.ocp.lab.net:8443/a1b2c3d4-e5f6-7890-abcd-ef1234567890/v2.0",
      "clientSecretRef": {
        "name": "odh-oidc-client-secret",
        "key": "clientSecret"
      }
    }
  }
}'
```

Verify the GatewayConfig is ready:

```bash
oc get gatewayconfig default-gateway
# NAME              READY   REASON
# default-gateway   True    Ready
```

## Step 5: Update the emulator seed config

Add the new client to `config.yaml` on the emulator VM so it survives a redeploy. The entry goes under the `clients:` list:

```yaml
  - client_id: "33333333-4444-5555-6666-777777777777"
    tenant_id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    display_name: "OpenDataHub Gateway"
    client_secret: "odh-client-secret"
    client_type: "confidential"
    redirect_uris:
      - "https://rh-ai.apps.ocp.lab.net/oauth2/callback"
    allowed_scopes:
      - "openid"
      - "profile"
      - "email"
      - "offline_access"
```

## OpenShift Resources Summary

| Resource | Namespace | Purpose |
|----------|-----------|---------|
| `Secret/odh-oidc-client-secret` | openshift-ingress | Client secret for the ODH gateway OIDC client |
| `Authentication/cluster` (patched) | cluster-scoped | Added `33333333-...` to audiences list |
| `GatewayConfig/default-gateway` (patched) | cluster-scoped | OIDC client config for the data science gateway |

## Gotchas

- **The audience must be in the Authentication CR** — if you create a new OIDC client for any component, its client ID must be added to `.spec.oidcProviders[0].issuer.audiences` in the Authentication CR. Otherwise kube-apiserver will reject tokens with that audience as 401 Unauthorized.
- **Adding an audience is safe** — it's additive. Existing tokens with other audience values continue to work. The kube-apiserver will restart to pick up the change but remains available.
- **`clientSecretRef` requires a `key` field** — the GatewayConfig validation requires both `name` and `key` in the secret reference. The key must match the data key in the secret (e.g. `clientSecret`).
- **`verifyProviderCertificate: false`** — required because the emulator uses a self-signed CA. In production with a real Entra ID, this would be `true`.
- **Redirect URI must match exactly** — the emulator validates the redirect URI against the registered list. The ODH gateway callback is `https://rh-ai.apps.ocp.lab.net/oauth2/callback`.
