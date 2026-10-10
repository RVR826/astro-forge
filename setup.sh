#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

###############################################################################
# Configuration
###############################################################################

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "ERROR: ${COMPOSE_FILE} not found."
    exit 1
fi

# Create .env with development defaults if it doesn't exist.
if [[ ! -f .env ]]; then
    echo "Creating .env..."

    cat > .env <<'EOF'
RUSTFS_ACCESS_KEY=rustfsadmin
RUSTFS_SECRET_KEY=rustfsadmin123
AWS_REGION=us-east-1
DEFAULT_S3_BUCKET_NAME=astro-forge
INCOMING_S3_BUCKET_NAME=incoming

POLARIS_DB_USER=polaris
POLARIS_DB_PASSWORD=polarisadmin123
POLARIS_DB_NAME=POLARIS
POLARIS_REALM=POLARIS
POLARIS_CLIENT_ID=root
POLARIS_CLIENT_SECRET=polarisadmin123

RUSTFS_VOLUME=/home/rvr826/data-lake/data/rustfs
POSTGRES_VOLUME=/home/rvr826/data-lake/data/postgres
SPARK_VOLUME=/home/rvr826/data-lake/data/spark
TRINO_VOLUME=/home/rvr826/data-lake/data/trino/catalog
EOF
    echo "Created .env"
fi

set -a
source .env
set +a

###############################################################################
# Helpers
###############################################################################

log() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

wait_for_command() {
    local description="$1"
    local attempts="$2"
    shift 2

    echo "Waiting for ${description}..."

    for ((i = 1; i <= attempts; i++)); do
        if "$@" >/dev/null 2>&1; then
            echo "${description} is ready."
            return 0
        fi

        sleep 2
    done

    echo "ERROR: ${description} did not become ready."
    return 1
}

###############################################################################
# Persistent directories
###############################################################################

log "Creating persistent directories"

mkdir -p \
    "${RUSTFS_VOLUME}" \
    "${POSTGRES_VOLUME}" \
    "${SPARK_VOLUME}" \
    "${TRINO_VOLUME}"
chmod 777 data/*

###############################################################################
# Start PostgreSQL and RustFS
###############################################################################

log "Starting PostgreSQL and RustFS"

docker compose -f "${COMPOSE_FILE}" up -d postgres rustfs

###############################################################################
# Determine Compose network
###############################################################################

POSTGRES_CONTAINER="$(docker compose -f "${COMPOSE_FILE}" ps -q postgres)"

if [[ -z "${POSTGRES_CONTAINER}" ]]; then
    echo "ERROR: Could not determine PostgreSQL container."
    exit 1
fi

NETWORK_NAME="$(
    docker inspect \
        --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' \
        "${POSTGRES_CONTAINER}"
)"

if [[ -z "${NETWORK_NAME}" ]]; then
    echo "ERROR: Could not determine Docker Compose network."
    exit 1
fi

echo "Using Docker network: ${NETWORK_NAME}"

###############################################################################
# Wait for PostgreSQL
###############################################################################

log "Waiting for PostgreSQL"

wait_for_command \
    "PostgreSQL" \
    30 \
    docker exec "${POSTGRES_CONTAINER}" \
        pg_isready \
        -U "${POLARIS_DB_USER}" \
        -d "${POLARIS_DB_NAME}"

log "Waiting for RustFS"

###############################################################################
# Create S3 bucket
###############################################################################

log "Ensuring default S3 bucket exists"

if docker run --rm \
    --network "${NETWORK_NAME}" \
    -e AWS_ACCESS_KEY_ID="${RUSTFS_ACCESS_KEY}" \
    -e AWS_SECRET_ACCESS_KEY="${RUSTFS_SECRET_KEY}" \
    -e AWS_DEFAULT_REGION="${AWS_REGION}" \
    amazon/aws-cli:latest \
    --endpoint-url "http://rustfs:9000" \
    s3api head-bucket \
    --bucket "${DEFAULT_S3_BUCKET_NAME}" >/dev/null 2>&1; then

    echo "Bucket '${DEFAULT_S3_BUCKET_NAME}' already exists."

else
    echo "Creating bucket '${DEFAULT_S3_BUCKET_NAME}'..."

    docker run --rm \
        --network "${NETWORK_NAME}" \
        -e AWS_ACCESS_KEY_ID="${RUSTFS_ACCESS_KEY}" \
        -e AWS_SECRET_ACCESS_KEY="${RUSTFS_SECRET_KEY}" \
        -e AWS_DEFAULT_REGION="${AWS_REGION}" \
        amazon/aws-cli:latest \
        --endpoint-url "http://rustfs:9000" \
        s3api create-bucket \
        --bucket "${DEFAULT_S3_BUCKET_NAME}"

    echo "Bucket created."
fi

log "Ensuring incoming S3 bucket exists"

if docker run --rm \
    --network "${NETWORK_NAME}" \
    -e AWS_ACCESS_KEY_ID="${RUSTFS_ACCESS_KEY}" \
    -e AWS_SECRET_ACCESS_KEY="${RUSTFS_SECRET_KEY}" \
    -e AWS_DEFAULT_REGION="${AWS_REGION}" \
    amazon/aws-cli:latest \
    --endpoint-url "http://rustfs:9000" \
    s3api head-bucket \
    --bucket "${INCOMING_S3_BUCKET_NAME}" >/dev/null 2>&1; then

    echo "Bucket '${INCOMING_S3_BUCKET_NAME}' already exists."

else
    echo "Creating bucket '${INCOMING_S3_BUCKET_NAME}'..."

    docker run --rm \
        --network "${NETWORK_NAME}" \
        -e AWS_ACCESS_KEY_ID="${RUSTFS_ACCESS_KEY}" \
        -e AWS_SECRET_ACCESS_KEY="${RUSTFS_SECRET_KEY}" \
        -e AWS_DEFAULT_REGION="${AWS_REGION}" \
        amazon/aws-cli:latest \
        --endpoint-url "http://rustfs:9000" \
        s3api create-bucket \
        --bucket "${INCOMING_S3_BUCKET_NAME}"

    echo "Bucket created."
fi

###############################################################################
# Bootstrap Polaris database
###############################################################################

log "Bootstrapping Polaris"

docker run --rm \
    --network "${NETWORK_NAME}" \
    -e POLARIS_PERSISTENCE_TYPE=relational-jdbc \
    -e QUARKUS_DATASOURCE_DB_KIND=postgresql \
    -e QUARKUS_DATASOURCE_USERNAME="${POLARIS_DB_USER}" \
    -e QUARKUS_DATASOURCE_PASSWORD="${POLARIS_DB_PASSWORD}" \
    -e QUARKUS_DATASOURCE_JDBC_URL="jdbc:postgresql://postgres:5432/${POLARIS_DB_NAME}" \
    -e POLARIS_REALM_CONTEXT_REALMS="${POLARIS_REALM}" \
    apache/polaris-admin-tool:1.7.0 \
    bootstrap \
    -r "${POLARIS_REALM}" \
    -c "${POLARIS_REALM},${POLARIS_CLIENT_ID},${POLARIS_CLIENT_SECRET}"

###############################################################################
# Start Polaris
###############################################################################

log "Starting Polaris"

docker compose -f "${COMPOSE_FILE}" up -d polaris

###############################################################################
# Wait for Polaris
###############################################################################

log "Waiting for Polaris"

POLARIS_TOKEN=""

for _ in {1..30}; do
    RESPONSE="$(
        curl -sS \
            -X POST \
            http://localhost:8181/api/catalog/v1/oauth/tokens \
            -H "Content-Type: application/x-www-form-urlencoded" \
            --data-urlencode "grant_type=client_credentials" \
            --data-urlencode "client_id=${POLARIS_CLIENT_ID}" \
            --data-urlencode "client_secret=${POLARIS_CLIENT_SECRET}" \
            --data-urlencode "scope=PRINCIPAL_ROLE:ALL" \
        2>/dev/null || true
    )"

    if [[ -n "${RESPONSE}" ]]; then
        POLARIS_TOKEN="$(
            printf '%s' "${RESPONSE}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'
        )"

        if [[ -n "${POLARIS_TOKEN}" ]]; then
            echo "Polaris is ready."
            break
        fi
    fi

    sleep 2
done

if [[ -z "${POLARIS_TOKEN}" ]]; then
    echo "ERROR: Polaris did not become ready."
    docker compose -f "${COMPOSE_FILE}" logs polaris
    exit 1
fi

export POLARIS_TOKEN
###############################################################################
# Configure Polaris
###############################################################################

log "Configuring Polaris catalog and roles"

# ---------------------------------------------------------------------------
# Create astronomy catalog
# ---------------------------------------------------------------------------

CATALOG_RESPONSE="$(
    curl -sS \
        -o /tmp/polaris_catalog_response.json \
        -w "%{http_code}" \
        -X POST \
        http://localhost:8181/api/management/v1/catalogs \
        -H "Authorization: Bearer ${POLARIS_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{
          \"catalog\": {
            \"name\": \"astronomy\",
            \"type\": \"INTERNAL\",
            \"properties\": {
              \"default-base-location\": \"s3://${DEFAULT_S3_BUCKET_NAME}/\"
            },
            \"storageConfigInfo\": {
              \"storageType\": \"S3\",
              \"endpoint\": \"http://localhost:9000\",
              \"endpointInternal\": \"http://rustfs:9000\",
              \"stsUnavailable\": true,
              \"pathStyleAccess\": true
            }
          }
        }"
)"

case "${CATALOG_RESPONSE}" in
    2*)
        echo "Astronomy catalog created."
        ;;
    409)
        echo "Astronomy catalog already exists."
        ;;
    *)
        echo "ERROR: Failed to create astronomy catalog."
        cat /tmp/polaris_catalog_response.json
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Create principal role
# ---------------------------------------------------------------------------

ROLE_RESPONSE="$(
    curl -sS \
        -o /tmp/polaris_role_response.json \
        -w "%{http_code}" \
        -X POST \
        http://localhost:8181/api/management/v1/principal-roles \
        -H "Authorization: Bearer ${POLARIS_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "principalRole": {
            "name": "catalog_manage_role"
          }
        }'
)"

case "${ROLE_RESPONSE}" in
    2*)
        echo "Principal role created."
        ;;
    409)
        echo "Principal role already exists."
        ;;
    *)
        echo "ERROR: Failed to create principal role."
        cat /tmp/polaris_role_response.json
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Create catalog role
# ---------------------------------------------------------------------------

CATALOG_ROLE_RESPONSE="$(
    curl -sS \
        -o /tmp/polaris_catalog_role_response.json \
        -w "%{http_code}" \
        -X POST \
        http://localhost:8181/api/management/v1/catalogs/astronomy/catalog-roles \
        -H "Authorization: Bearer ${POLARIS_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "catalogRole": {
            "name": "astronomy_admin"
          }
        }'
)"

case "${CATALOG_ROLE_RESPONSE}" in
    2*)
        echo "Catalog role created."
        ;;
    409)
        echo "Catalog role already exists."
        ;;
    *)
        echo "ERROR: Failed to create catalog role."
        cat /tmp/polaris_catalog_role_response.json
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Assign catalog role to principal role
# ---------------------------------------------------------------------------

ROLE_ASSIGNMENT_STATUS="$(
  curl -sS \
    -o /tmp/polaris_role_assignment_response.json \
    -w "%{http_code}" \
    -X PUT \
    http://localhost:8181/api/management/v1/principal-roles/catalog_manage_role/catalog-roles/astronomy \
    -H "Authorization: Bearer ${POLARIS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
      "catalogRole": {
        "name": "astronomy_admin"
      }
    }'
)"

case "${ROLE_ASSIGNMENT_STATUS}" in
  2*)
    echo "Catalog role 'astronomy_admin' assigned to 'catalog_manage_role'."
    ;;
  409)
    echo "Catalog role 'astronomy_admin' is already assigned to 'catalog_manage_role'."
    ;;
  *)
    echo "ERROR: Failed to assign catalog role (HTTP ${ROLE_ASSIGNMENT_STATUS})."
    cat /tmp/polaris_role_assignment_response.json
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# Assign principal role to root
# ---------------------------------------------------------------------------

ROOT_ASSIGNMENT_STATUS="$(
  curl -sS \
    -o /tmp/polaris_root_assignment_response.json \
    -w "%{http_code}" \
    -X PUT \
    http://localhost:8181/api/management/v1/principals/root/principal-roles \
    -H "Authorization: Bearer ${POLARIS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
      "principalRole": {
        "name": "catalog_manage_role"
      }
    }'
)"

case "${ROOT_ASSIGNMENT_STATUS}" in
  2*)
    echo "Principal role 'catalog_manage_role' assigned to 'root'."
    ;;
  409)
    echo "Principal role 'catalog_manage_role' is already assigned to 'root'."
    ;;
  *)
    echo "ERROR: Failed to assign principal role (HTTP ${ROOT_ASSIGNMENT_STATUS})."
    cat /tmp/polaris_root_assignment_response.json
    exit 1
    ;;
esac

###############################################################################
# Generate Trino Polaris catalog configuration
###############################################################################

log "Configuring Trino"

mkdir -p "${TRINO_VOLUME}"

cat > "${TRINO_VOLUME}/polaris.properties" <<EOF
connector.name=iceberg

iceberg.catalog.type=rest
iceberg.rest-catalog.uri=http://polaris:8181/api/catalog
iceberg.rest-catalog.warehouse=astronomy

iceberg.rest-catalog.security=OAUTH2
iceberg.rest-catalog.oauth2.credential=${POLARIS_CLIENT_ID}:${POLARIS_CLIENT_SECRET}
iceberg.rest-catalog.oauth2.scope=PRINCIPAL_ROLE:ALL
iceberg.rest-catalog.oauth2.server-uri=http://polaris:8181/api/catalog/v1/oauth/tokens

iceberg.rest-catalog.http-headers=Polaris-Realm: ${POLARIS_REALM}
iceberg.rest-catalog.vended-credentials-enabled=false

fs.s3.enabled=true
s3.endpoint=http://rustfs:9000
s3.path-style-access=true
s3.region=${AWS_REGION}
EOF

echo "Generated:"
echo "  ${TRINO_VOLUME}/polaris.properties"

###############################################################################
# Start Trino
###############################################################################

log "Starting Trino"

docker compose -f "${COMPOSE_FILE}" up -d trino

###############################################################################
# Build and start Spark
###############################################################################

log "Starting Spark"

docker compose -f "${COMPOSE_FILE}" build spark
docker compose -f "${COMPOSE_FILE}" up -d spark

###############################################################################
# Build and start Agent
###############################################################################

log "Starting Agent"

docker compose -f "${COMPOSE_FILE}" build agent
docker compose -f "${COMPOSE_FILE}" up -d agent

###############################################################################
# Final status
###############################################################################

log "AstroForge deployment complete"
docker compose -f "${COMPOSE_FILE}" ps

echo
echo "Services:"
echo "  RustFS:    http://localhost:9000"
echo "  Polaris:   http://localhost:8181"
echo "  Trino:     http://localhost:8080"
echo
echo "S3 buckets:"
echo "  (Default)s3://${DEFAULT_S3_BUCKET_NAME}"
echo "  (Incoming) s3://${INCOMING_S3_BUCKET_NAME}"
echo
echo "Polaris catalog:"
echo "  astronomy"
echo
echo "Spark:"
echo "  /workspace/jobs/spark_config.py"

echo "Agent:"
echo "  /app/main.py"
echo
echo "Deployment completed successfully."