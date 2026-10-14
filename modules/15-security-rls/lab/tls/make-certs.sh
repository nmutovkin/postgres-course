#!/usr/bin/env bash
# Module 15 TLS lab: create a private CA, a server certificate and a client
# certificate in ./certs. openssl runs inside the postgres:17 image, so you
# don't need it installed locally.
#
#   ./make-certs.sh            # creates certs/ (refuses to overwrite)
#   rm -rf certs && ./make-certs.sh   # start over
#
# Files (course lab only; never commit real keys):
#   certs/ca.crt / ca.key           the lab's certificate authority ("Bazaar Lab CA")
#   certs/server.crt / server.key   PostgreSQL's certificate: CN and SAN = db.bazaar.internal
#   certs/alice.crt / alice.key     a client certificate with CN = alice (for "cert" authentication)
set -euo pipefail
cd "$(dirname "$0")"
if [[ -e certs/ca.crt ]]; then
  echo "certs/ already exists; remove it first to regenerate" >&2
  exit 1
fi
mkdir -p certs

docker run --rm -v "$PWD/certs:/certs" -w /certs postgres:17 bash -euo pipefail -c '
# 1. The certificate authority: a self-signed certificate whose key signs the others.
openssl req -new -x509 -nodes -days 3650 -newkey rsa:2048 \
    -subj "/CN=Bazaar Lab CA" -keyout ca.key -out ca.crt 2>/dev/null

# 2. The server: key + signing request, then signed by the CA.
#    verify-full checks the host name the client used against the SAN list.
openssl req -new -nodes -newkey rsa:2048 \
    -subj "/CN=db.bazaar.internal" -keyout server.key -out server.csr 2>/dev/null
printf "subjectAltName=DNS:db.bazaar.internal,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n" > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days 825 -extfile server.ext -out server.crt 2>/dev/null

# 3. A client certificate for user alice: the CN is the PostgreSQL role name.
openssl req -new -nodes -newkey rsa:2048 \
    -subj "/CN=alice" -keyout alice.key -out alice.csr 2>/dev/null
printf "extendedKeyUsage=clientAuth\n" > alice.ext
openssl x509 -req -in alice.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days 825 -extfile alice.ext -out alice.crt 2>/dev/null

rm -f ./*.csr ./*.ext ./*.srl
chmod 600 ./*.key
chmod 644 ./*.crt
openssl x509 -in server.crt -noout -subject -issuer -ext subjectAltName
'
ls -l certs
