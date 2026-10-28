Public intermediate CA served with this package
===============================================

globalsign-gcc-r46-ov-tls-ca-2025.pem
    Subject: C=BE, O=GlobalSign nv-sa, CN=GlobalSign GCC R46 OV TLS CA 2025
    Issuer:  C=BE, O=GlobalSign nv-sa, CN=GlobalSign Root R46
    Validity: 2025-09-17 .. 2029-06-23
    SHA-256:  829aa5cdae79bc3e1245d2b3b634be767524b44bc0a403d0afaef97e66985925

Why it is shipped: www.elections.alaska.gov (Alaska Division of Elections)
sends a leaf issued by this CA but only the older "GlobalSign RSA OV SSL CA
2018" intermediate, so a client that verifies against the public bundle alone
fails with "unable to verify the first certificate". electionwatch/sources/
results/ak_doe.py trusts this file *in addition to* certifi for that host only;
verification is never disabled.

Provenance: downloaded 2026-10-27 from the CA Issuers URL printed in the site's
leaf certificate, http://secure.globalsign.com/cacert/gsgccr46ovtlsca2025.crt
(DER; converted to PEM with `openssl x509 -inform der -in ... -out ...`). It is
a public certificate, not a secret. Delete it once the Division serves the
complete chain (the module then still works: certifi covers it).
