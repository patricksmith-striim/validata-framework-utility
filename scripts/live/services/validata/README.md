# Validata image recipe

One Validata server: `slt-validata` (web UI and API on 9080 in the container, 9180 on the host by default, login
`admin` / `validata`). No cluster and no agents: Validata runs on a single node.

How it is built, the way [../striim](../striim/README.md) builds Striim:

- `download-dependencies.sh` fetches the `validata-dbms` and `validata-node` `.deb` packages for `VALIDATA_VERSION`
  (default 1.1.0, about 0.9 GB, resumable) into `images/validata/deps/` (not in git).
- `images/validata/Dockerfile` builds an amd64 image from them: `ubuntu:22.04`, JDK 17, `dpkg -i`.
- `images/validata/files/entrypoint.sh` starts Derby (the historian), writes the license and names into
  `conf/startUp.properties`, runs `sksConfig.sh` once per conf volume, then starts the server.
- The license is never baked in: `VALIDATA_COMPANY_NAME`, `VALIDATA_CLUSTER_NAME`, `VALIDATA_PRODUCT_KEY` and
  `VALIDATA_LICENCE_KEY` come from the environment at start.
- CPU and memory caps come from `SLT_VALIDATA_CPUS`, `SLT_VALIDATA_MEM_MAX` and `SLT_VALIDATA_MEM_LIMIT`; the host
  ports from `SLT_VALIDATA_HTTP_HOST_PORT` and `SLT_VALIDATA_HTTPS_HOST_PORT`.

By hand, from this folder:

```
./download-dependencies.sh
VALIDATA_COMPANY_NAME=Acme VALIDATA_CLUSTER_NAME=validata-lab docker compose up -d --build slt-validata
```
