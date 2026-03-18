# Configuring OCP 4.21 BYOIDC with Entra ID Mock

This documents all the OpenShift cluster-side configuration needed to use the Entra ID mock server as an external OIDC identity provider via BYOIDC (TechPreview).

## Prerequisites

- OCP 4.21 cluster running on libvirt
- Entra mock server running at `https://entra.ocp.lab.net:8443`
- CA certificate at `ocp-work/certs/ca.crt` (signs the entra mock's TLS cert)
- Entra mock configured with:
  - Console client: `11111111-2222-3333-4444-555555555555` (confidential)
  - CLI client: `22222222-3333-4444-5555-666666666666` (public)
  - Admins group: `g1g1g1g1-g1g1-g1g1-g1g1-g1g1g1g1g1g1`

## Step 1: Enable TechPreview

BYOIDC requires the TechPreviewNoUpgrade feature gate. This triggers node reboots and takes 10-20 minutes to settle.

```bash
oc patch featuregate cluster --type=merge \
  -p '{"spec":{"featureSet":"TechPreviewNoUpgrade"}}'

# Wait for machine config pools to finish rolling out
watch oc get mcp
```

## Step 2: Create cluster-admin rolebinding for OIDC group

Do this BEFORE changing authentication so OIDC users will have admin access once auth switches over. The entra mock puts group UUIDs (not names) in the `groups` claim.

```bash
oc create clusterrolebinding oidc-cluster-admins \
    --clusterrole=cluster-admin \
    --group=g1g1g1g1-g1g1-g1g1-g1g1-g1g1g1g1g1g1
```

## Step 3: Create client secret

The console client is confidential and needs its secret stored in the cluster.

```bash
oc create secret generic console-secret \
    --from-literal=clientSecret=my-client-secret \
    -n openshift-config
```

## Step 4: Create CA configmap

The kube-apiserver needs to trust the entra mock's TLS certificate (signed by our CA).

```bash
oc create configmap entra-ca \
    --from-file=ca-bundle.crt=ocp-work/certs/ca.crt \
    -n openshift-config
```

## Step 5: Apply OIDC Authentication CR

The existing Authentication CR has a `webhookTokenAuthenticator` field that conflicts with `type: OIDC`. Use `oc replace` instead of `oc apply` to fully replace the spec.

```bash
oc get authentication cluster -o json | python3 -c "
import sys, json
d = json.load(sys.stdin)
d['spec'] = {
    'type': 'OIDC',
    'oidcProviders': [{
        'name': 'entra-id-mock',
        'claimMappings': {
            'groups': {'claim': 'groups', 'prefixPolicy': 'NoPrefix'},
            'username': {'claim': 'preferred_username', 'prefixPolicy': 'NoPrefix'}
        },
        'issuer': {
            'audiences': [
                '11111111-2222-3333-4444-555555555555',
                '22222222-3333-4444-5555-666666666666'
            ],
            'issuerURL': 'https://entra.ocp.lab.net:8443/a1b2c3d4-e5f6-7890-abcd-ef1234567890/v2.0',
            'issuerCertificateAuthority': {'name': 'entra-ca'}
        },
        'oidcClients': [
            {
                'clientID': '22222222-3333-4444-5555-666666666666',
                'componentName': 'cli',
                'componentNamespace': 'openshift-console'
            },
            {
                'clientID': '11111111-2222-3333-4444-555555555555',
                'clientSecret': {'name': 'console-secret'},
                'componentName': 'console',
                'componentNamespace': 'openshift-console'
            }
        ]
    }]
}
json.dump(d, sys.stdout)
" | oc replace -f -
```

**Why `oc replace` instead of `oc apply`?** The default Authentication CR contains `spec.webhookTokenAuthenticator` which is invalid when `spec.type` is set to `OIDC`. Using `oc apply` tries to merge and fails validation. `oc replace` overwrites the entire spec cleanly.

## Step 6: Wait for operators to settle

The kube-apiserver operator will restart API server pods to pick up the OIDC configuration. This takes a few minutes.

```bash
watch oc get co kube-apiserver authentication
```

Both should show `AVAILABLE=True`, `PROGRESSING=False`, `DEGRADED=False`.

## Step 7: Login via CLI

```bash
oc login \
    --exec-plugin=oc-oidc \
    --issuer-url=https://entra.ocp.lab.net:8443/a1b2c3d4-e5f6-7890-abcd-ef1234567890/v2.0 \
    --client-id=22222222-3333-4444-5555-666666666666 \
    --extra-scopes=email,profile \
    --oidc-certificate-authority=ocp-work/certs/ca.crt \
    --callback-port=8080 \
    https://api.ocp.lab.net:6443
```

Credentials: `kubeadmin@contoso.onmicrosoft.com` / `changeme`

## Authentication CR Reference

The final Authentication CR looks like this:

```yaml
apiVersion: config.openshift.io/v1
kind: Authentication
metadata:
  name: cluster
spec:
  type: OIDC
  oidcProviders:
  - name: entra-id-mock
    claimMappings:
      groups:
        claim: groups
        prefixPolicy: NoPrefix
      username:
        claim: preferred_username
        prefixPolicy: NoPrefix
    issuer:
      audiences:
      - "11111111-2222-3333-4444-555555555555"
      - "22222222-3333-4444-5555-666666666666"
      issuerURL: https://entra.ocp.lab.net:8443/a1b2c3d4-e5f6-7890-abcd-ef1234567890/v2.0
      issuerCertificateAuthority:
        name: entra-ca
    oidcClients:
    - clientID: "22222222-3333-4444-5555-666666666666"
      componentName: cli
      componentNamespace: openshift-console
    - clientID: "11111111-2222-3333-4444-555555555555"
      clientSecret:
        name: console-secret
      componentName: console
      componentNamespace: openshift-console
```

## OpenShift Resources Summary

| Resource | Namespace | Purpose |
|----------|-----------|---------|
| `FeatureGate/cluster` | cluster-scoped | Enables TechPreviewNoUpgrade for BYOIDC support |
| `Authentication/cluster` | cluster-scoped | Configures OIDC provider, claim mappings, and clients |
| `Secret/console-secret` | openshift-config | Client secret for the confidential console client |
| `ConfigMap/entra-ca` | openshift-config | CA certificate bundle so kube-apiserver trusts the entra mock |
| `ClusterRoleBinding/oidc-cluster-admins` | cluster-scoped | Maps OIDC Admins group UUID to cluster-admin role |

## Gotchas

- **Create the rolebinding before switching auth** - otherwise you may lock yourself out. The install kubeconfig (`ocp-install/auth/kubeconfig`) uses client certs (`system:admin`) and always works as a fallback.
- **`oc apply` won't work on the Authentication CR** - use `oc replace` to remove the conflicting `webhookTokenAuthenticator` field.
- **Scopes must include `profile`** - the `preferred_username` claim (used for username mapping) is only included in tokens when the `profile` scope is requested. Without it, kube-apiserver rejects the token with 401.
- **oc-oidc caches tokens** in `~/.kube/cache/oc/`. If you change scopes or token lifetimes, delete the cache file to force a fresh login.
- **CA trust for the CLI** - set `SSL_CERT_FILE` or use `--oidc-certificate-authority` so the `oc-oidc` exec plugin trusts the entra mock's self-signed cert.
- **Groups claim uses UUIDs** - the entra mock puts group IDs (not names) in the `groups` claim. ClusterRoleBindings must reference the UUID.
