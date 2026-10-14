# ansible-role-elasticsearch

An Ansible role that installs and configures an Elasticsearch 9.x cluster, single-node or multi-node, on RHEL, AlmaLinux and Rocky Linux 9 and 10.

What it does:

- Installs a pinned Elasticsearch RPM from the Elastic repository. The repo is left disabled so a normal `dnf update` can't upgrade it.
- Sets per-node roles and JVM heap from `host_vars`.
- Sets up TLS on the HTTP and transport layers using PEM certificates from the `ssl_certificates` host variable. The private key is vaulted.
- Configures an Active Directory realm, with the bind password stored in the Elasticsearch keystore.
- Creates security roles and role mappings through the REST API. Runs are idempotent.
- Installs, removes and upgrades plugins such as `mapper-size`.
- Installs a license from a variable.
- **GeoIP processor:** manages the GeoIP downloader, installs your own `.mmdb` databases on ingest nodes, and creates ingest pipelines through the REST API. See [GeoIP processor and ingest pipelines](#geoip-processor-and-ingest-pipelines).
- **Rolling restarts:** changes to a running cluster restart the nodes in batches of limited size, one batch at a time. Shard allocation is handled and the role waits for cluster health between batches. Version upgrades go through the same path.
- **Slow networks:** retries and async downloads let it work over high-latency, low-bandwidth links such as satellite. See [Slow and unreliable networks](#slow-and-unreliable-networks).

## Requirements

- **Control node:** ansible-core 2.15 or later, plus the `ansible.posix` collection:

  ```bash
  ansible-galaxy collection install -r requirements.yml
  ```

- **Target hosts:** RHEL, AlmaLinux or Rocky Linux 9 or 10 (see [Supported platforms](#supported-platforms)), with `openssl` installed. On STIG-hardened hosts see [Hardened hosts](#hardened-hosts-fips-selinux-fapolicyd) — fapolicyd has to allow Ansible before anything runs. They need a package source: Elastic's repository (`artifacts.elastic.co`) or a mirror, or a Red Hat Satellite or Katello server. See [Package source](#package-source).
- **Name resolution** between nodes: inventory hostnames are used for discovery and API calls unless you override them.
- **Certificates.** Each node certificate must:
  - include the node's inventory hostname (or `elasticsearch_api_host`) in its SAN, because `verification_mode: full` is the default;
  - include the node's IP address in its SAN too, because nodes connect to each other's publish address;
  - have both `serverAuth` and `clientAuth` extended key usage, because the same certificate is used for transport TLS.
- **Active Directory** needs a Platinum, Enterprise or trial license. With a basic license Elasticsearch starts, but the AD realm is disabled.

## Supported platforms

| Distribution | `ansible_facts['distribution']` | Major versions |
|---|---|---|
| Red Hat Enterprise Linux | `RedHat` | 9, 10 |
| AlmaLinux | `AlmaLinux` | 9, 10 |
| Rocky Linux | `Rocky` | 9, 10 |

- **Same tasks everywhere.** The role has no distribution-specific tasks, and Elasticsearch comes from Elastic's RPM packages on all of them.
- **Other distributions.** Preflight stops the run on any other distribution or major version. To try another Enterprise Linux 9 or 10 rebuild, such as CentOS Stream (`CentOS`) or Oracle Linux (`OracleLinux`), add its distribution fact to `elasticsearch_supported_distributions`. Those aren't tested.
- **Satellite or Katello.** `elasticsearch_repo_source: satellite` needs `subscription-manager` and a host registered to the server. RHEL hosts can use Red Hat Satellite or Katello. AlmaLinux and Rocky Linux hosts are normally registered to Katello; check your Satellite version's supported host operating systems before using it for them.
- **Elastic's support.** See [Elastic's support matrix](https://www.elastic.co/support/matrix) for the operating systems Elastic supports for your Elasticsearch release.

## Installation

Add the role to your project's `requirements.yml`:

```yaml
roles:
  - name: elasticsearch
    src: https://github.com/jmaughmer/ansible-role-elasticsearch.git
    scm: git
    version: main          # or a release tag

collections:
  - name: ansible.posix
    version: ">=1.5.0"
```

```bash
ansible-galaxy install -r requirements.yml
```

## Package source

`elasticsearch_repo_source` selects where the `elasticsearch` package comes from:

| Value | Package source |
|---|---|
| `elastic` (default) | Elastic's public repository, or a mirror set with `elasticsearch_repo_baseurl` and `elasticsearch_repo_gpgkey`. The role writes a disabled repository file and enables it only for its own dnf tasks, so a routine `dnf update` never upgrades Elasticsearch. |
| `satellite` | A repository synced into Red Hat Satellite or Katello, delivered to registered hosts through subscription-manager. See [Red Hat Satellite or Katello](#red-hat-satellite-or-katello). |
| `none` | Repositories you manage yourself. The role installs from whatever is enabled on the host. |

**Version check.** Before installing or upgrading, the role checks that `elasticsearch_version` is available from the package source, so a missing version fails before anything changes. You can turn this off with `elasticsearch_package_version_check`.

**Migrating.** `elasticsearch_repo_source` replaces `elasticsearch_manage_repo`; use `elasticsearch_repo_source: none` where you had `false`. Preflight fails if the old variable is still set.

### Red Hat Satellite or Katello

One Satellite repository of Elastic's packages serves all three roles: Elasticsearch, Logstash and Kibana.

**On Satellite:**
1. Create a product, for example `Elastic`.
2. Create a content credential from Elastic's GPG key (`https://artifacts.elastic.co/GPG-KEY-elasticsearch`), so hosts verify package signatures.
3. In that product, create a yum repository with the upstream URL `https://artifacts.elastic.co/packages/9.x/yum` and the content credential, then sync it.
4. Add the repository to the content view the hosts use, then publish and promote it to their lifecycle environment.
5. Make sure the hosts' activation key gives them access to the product.

**On a registered host,** find the repository ID. It is normally `<organization label>_<product label>_<repository label>`:

```bash
subscription-manager repos --list | grep -i -A4 elastic
```

**Role variables:**

```yaml
elasticsearch_repo_source: satellite
elasticsearch_satellite_repo_id: ACME_Elastic_Elastic_9_x

# Optional
elasticsearch_satellite_repo_state: disabled      # unmanaged (default), enabled or disabled
elasticsearch_satellite_repo_overrides:           # applied with subscription-manager repo-override
  timeout: "600"
  retries: "20"
elasticsearch_satellite_gpgkey: ""                # extra key to import (URL or path on the host)
```

**What the role does in `satellite` mode:**
- **No repository file.** subscription-manager owns `redhat.repo`. The role reads it on the host, without contacting Satellite, and passes the repository to dnf with `enablerepo`. Installs and upgrades work whether the repository is enabled or disabled.
- **Checks that `subscription-manager` is installed**, and fails with a clear message if it isn't.
- **Removes its own Elastic repository file** if one is left over from `elastic` mode, so packages don't come from two sources.
- **Checks the repository exists.** If it doesn't, the role runs `subscription-manager refresh` once (`elasticsearch_satellite_refresh`), then fails with the related repository IDs it can see.
- **Enabled state** (`elasticsearch_satellite_repo_state`):
  - `unmanaged` leaves it to Satellite.
  - `enabled` or `disabled` sets it with `subscription-manager repos`.
  - `disabled` keeps routine `dnf update` from upgrading Elasticsearch, like `elastic` mode does.
- **Content overrides** (`elasticsearch_satellite_repo_overrides`) are applied with `subscription-manager repo-override` when a value differs. Use them for dnf options such as `timeout`, `retries` and `minrate`, because `redhat.repo` can't be edited directly. `enabled`, `baseurl` and `name` aren't allowed.
- **GPG keys.** dnf imports the key assigned to the repository in Satellite. Set `elasticsearch_satellite_gpgkey` to import a different key, from a URL or a file on the host.

Enabled-state changes and content overrides made with `subscription-manager` are stored on the Satellite server, as part of that host's settings.

## Quick start

[`examples/`](examples/) is a complete deployment for a three-node cluster:

```
examples/
├── ansible.cfg                      # SSH settings for slow links, roles/collections paths
├── requirements.yml                 # this role + ansible.posix
├── playbook.yml
└── inventory/
    ├── hosts.yml
    ├── group_vars/all/network.yml   # retries/timeouts for satellite links
    ├── group_vars/elasticsearch/
    │   ├── main.yml                 # cluster settings, AD, roles, mappings, plugins
    │   └── vault.yml                # secrets (encrypt with ansible-vault)
    └── host_vars/
        └── es-data-N.example.com.yml   # node roles, heap, ssl_certificates
```

```bash
cd examples
ansible-galaxy install -r requirements.yml
# edit inventory/, then:
ansible-vault encrypt inventory/group_vars/elasticsearch/vault.yml
ansible-playbook playbook.yml --ask-vault-pass
```

To run the examples against your working copy instead of the published role, link it into place instead of installing it:

```bash
mkdir -p roles && ln -s ../.. roles/elasticsearch
```

**Playbook requirements.**
- Don't set `serial` on the play. The role orchestrates restarts across the whole play itself, and preflight fails if the play is serialized.
- Keep `any_errors_fatal: true` so a failure on one node, such as a bad certificate, stops the run before any restarts.

Useful tags: `elasticsearch_config`, `elasticsearch_tls`, `elasticsearch_plugins`, `elasticsearch_security`, `elasticsearch_license`, `elasticsearch_geoip`, `elasticsearch_ingest`. The fact-gathering and preflight tasks always run.

## Certificates: `ssl_certificates`

Certificates are supplied per host, with the private key vaulted:

```yaml
# host_vars/es-data-1.example.com.yml
ssl_certificates:
  - name: "es-data-1.example.com"
    certificate:
      content: |
        -----BEGIN CERTIFICATE-----
        ...
    key:
      content: !vault |
        $ANSIBLE_VAULT;1.1;AES256
        ...
      # passphrase: !vault | ...             # only for encrypted PEM keys
    # ca:
    #   content: |                           # optional, overrides elasticsearch_ssl_ca_certificate
```

The role uses the entry named `elasticsearch_ssl_certificate_name`, which defaults to the inventory hostname. To vault a key:

```bash
ansible-vault encrypt_string --stdin-name content < es-data-1.example.com.key
```

### Certificates issued by ansible-role-elastic-tls

[ansible-role-elastic-tls](https://github.com/jmaughmer/ansible-role-elastic-tls) can issue these certificates instead, from a private CA it creates and keeps, or from
Let's Encrypt. Run it first in the same play:

```yaml
  roles:
    - role: elastic_tls
    - role: elasticsearch
```

It publishes what it issued as `elastic_tls_ssl_certificates`, which this role reads **after**
`ssl_certificates`. A manual entry with the same name always wins, so hosts can be moved across one
at a time. Match the name this role looks for: ``elasticsearch_ssl_certificate_name``.

Entries in either list can point at a file already on the host instead of carrying its content:

```yaml
ssl_certificates:
  - name: es-data-1.example.com
    certificate: {path: /etc/pki/ansible-tls/certs/es-data-1.example.com.crt}
    key: {path: /etc/pki/ansible-tls/private/es-data-1.example.com.key}
    ca: {path: /etc/pki/ansible-tls/certs/ca.crt}
```

``elasticsearch_ssl_ca_certificate_path`` does the same for the CA when it is not part of the entry. Inline content wins when an
entry carries both.

Note that Let's Encrypt issues server certificates only. Transport TLS uses one certificate at both ends of a connection, so node certificates need client authentication too and have to come from the private CA.

## Per-node settings (host_vars)

```yaml
elasticsearch_node_roles: [master]          # [] = coordinating-only
elasticsearch_heap_size: "4g"               # or "auto"
```

| Variable | Default | Purpose |
|---|---|---|
| `elasticsearch_node_name` | inventory hostname | `node.name` |
| `elasticsearch_discovery_address` | inventory hostname | Address other nodes use in `discovery.seed_hosts` |
| `elasticsearch_ssl_certificate_name` | inventory hostname | Which `ssl_certificates` entry to use |
| `elasticsearch_node_attributes` | `{}` | `node.attr.*`, for example `{rack: r1}` |
| `elasticsearch_jvm_options` | `[]` | Extra lines for `jvm.options.d/ansible.options` |

Set `elasticsearch_node_roles` and `elasticsearch_node_name` in inventory (`host_vars` or `group_vars`), not with `-e` or play vars. The role reads them for *every* host in the cluster group to build discovery settings.

## Cluster-wide settings (group_vars)

[`defaults/main.yml`](defaults/main.yml) documents every variable. The main ones:

| Variable | Notes |
|---|---|
| `elasticsearch_version` | Exact 9.x.y release. Changing it on a running cluster starts a rolling upgrade. |
| `elasticsearch_cluster_name`, `elasticsearch_cluster_group` | The group must contain every node in the cluster. |
| `elasticsearch_elastic_password` | Vault it. Used as `bootstrap.password` on new nodes and for all API calls. |
| `elasticsearch_ssl_ca_certificate` | PEM of the CA that issued the node certificates. |
| `elasticsearch_license` | License JSON from Elastic, as a dict or string. It's only applied when its `uid` differs from the installed license. |
| `elasticsearch_plugins` | `- mapper-size`, or `{name: x, source: https://.../x.zip}` |
| `elasticsearch_plugins_purge` | Remove plugins that aren't listed. |
| `elasticsearch_rolling_restart_batch_size` | Maximum number of non-master nodes restarted at once. |
| `elasticsearch_rolling_restart_master_batch_size` | Maximum number of master-eligible nodes restarted at once. Must be less than half the master-eligible nodes. |
| `elasticsearch_cluster_health_status` | Health required between batches: `green`, or `yellow` for single-node clusters. |
| `elasticsearch_restart_on_change` | Set to `false` to only report pending restarts. |
| `elasticsearch_force_restart` | Rolling-restart every running node in the play. |

### Active Directory, roles and role mappings

```yaml
elasticsearch_ad_enabled: true
elasticsearch_ad_realm_name: corp_ad
elasticsearch_ad_domain_name: example.com
elasticsearch_ad_urls: ["ldaps://dc1.example.com:636", "ldaps://dc2.example.com:636"]
elasticsearch_ad_bind_dn: svc-elasticsearch@example.com
elasticsearch_ad_bind_password: "{{ vault_elasticsearch_ad_bind_password }}"
elasticsearch_ad_group_search_base_dn: "OU=Groups,DC=example,DC=com"
elasticsearch_ad_ssl_ca_certificate: |      # optional; defaults to the node CA
  -----BEGIN CERTIFICATE-----

elasticsearch_security_roles:
  - name: logs_reader
    definition:                               # body of PUT _security/role/<name>
      cluster: [monitor]
      indices:
        - names: ["logs-*"]
          privileges: [read, view_index_metadata]

elasticsearch_security_role_mappings:
  - name: corp_ad_admins
    definition:                               # body of PUT _security/role_mapping/<name>
      enabled: true
      roles: [superuser]
      rules:
        all:
          - field: { realm.name: corp_ad }
          - field: { groups: "CN=ES-Admins,OU=Groups,DC=example,DC=com" }
  - name: old_mapping
    state: absent
```

The file and native realms are always configured explicitly, with orders 0 and 1, so the `elastic` user keeps working when the AD realm is added.

The role stores a SHA-1 of each definition in `metadata.ansible_managed_hash`, and only sends a `PUT` when that hash changes.

### GeoIP processor and ingest pipelines

The [geoip processor](https://www.elastic.co/docs/reference/enrich-processor/geoip-processor) adds location data from an IP address. It runs on ingest nodes and needs IP geolocation databases, which can come from three places:

| Database source | Settings |
|---|---|
| **Elastic's GeoIP downloader** (default). The cluster downloads GeoLite2 City, Country and ASN from `geoip.elastic.co` and keeps them up to date. Nodes need HTTPS access to it. | `elasticsearch_geoip_downloader_enabled: true` |
| **Your own mirror** of the downloader endpoint, built with `bin/elasticsearch-geoip` and served over HTTP(S). | `elasticsearch_geoip_downloader_endpoint` |
| **Database files** you supply, such as GeoLite2 for air-gapped clusters or commercial MaxMind GeoIP2 and IPinfo databases. | `elasticsearch_geoip_databases` |

```yaml
# Air-gapped cluster: no downloads, databases shipped by Ansible
elasticsearch_geoip_downloader_enabled: false
elasticsearch_geoip_databases:
  - name: GeoLite2-City.mmdb
    src: geoip/GeoLite2-City.mmdb             # control node, playbook files/ search path
  - name: GeoIP2-ISP.mmdb
    url: https://repo.example.com/geoip/GeoIP2-ISP.mmdb
    checksum: "sha256:0f3c..."                # replaced when the checksum changes
  - name: GeoLite2-ASN.mmdb
    state: absent

elasticsearch_ingest_pipelines:
  - name: geoip-client
    definition:                               # body of PUT _ingest/pipeline/<name>
      description: Add client location
      processors:
        - geoip:
            field: client.ip
            target_field: client.geo
            ignore_missing: true
        - geoip:
            field: client.ip
            target_field: client.isp
            database_file: GeoIP2-ISP.mmdb
            ignore_missing: true
  - name: old-pipeline
    state: absent
```

**Downloader settings.** `elasticsearch_geoip_downloader_enabled`, `elasticsearch_geoip_downloader_eager_download` and `elasticsearch_geoip_downloader_poll_interval` are dynamic cluster settings. The role sets them as persistent settings through the API, so changing them doesn't restart nodes.
- `eager_download: true` downloads the databases right away, rather than when the first pipeline with a geoip processor is created.
- Disabling the downloader also deletes the databases it downloaded.
- `elasticsearch_geoip_downloader_endpoint` is a static setting in `elasticsearch.yml`, so changing it restarts nodes.

**Database files.**
- **Where they go.** Files are installed in `/etc/elasticsearch/ingest-geoip` on nodes with the `ingest` role, or on every node with `elasticsearch_geoip_databases_all_nodes: true`. Use a file's name as the processor's `database_file`.
- **Each entry** needs `src` (a file on the control node) or `url` (downloaded by the node).
  - A `src` file is copied only when its checksum differs from the node's copy.
  - A `url` file is downloaded once. Add `checksum` so the role replaces it when the checksum changes.
- **Removing files.** `state: absent` removes a file, and `elasticsearch_geoip_databases_purge: true` removes every `.mmdb` file that isn't listed.
- **No restart needed.** Elasticsearch watches the directory and loads, reloads and unloads files on its own. Set `elasticsearch_geoip_restart_on_change: true` to rolling-restart nodes instead.
- **Same names as downloaded databases.** Elasticsearch uses a downloaded database over a file with the same name. To use your own `GeoLite2-City.mmdb`, `GeoLite2-Country.mmdb` or `GeoLite2-ASN.mmdb`, disable the downloader. Preflight warns when they clash.

**Ingest pipelines** are managed like security roles. The role stores a SHA-1 of each definition in `_meta.ansible_managed_hash` and only sends a `PUT` when it changes. Downloader settings are applied before pipelines, so a disabled downloader never starts a download when a geoip pipeline is created.

To check which databases each node has loaded:

```bash
curl -s -u elastic --cacert ca.crt "https://es-data-1.example.com:9200/_ingest/geoip/stats?pretty"
```

## How a run works

1. **Every host in parallel.** The role gathers state (installed version, whether the service is running, whether `path.data/_state` exists) and validates everything. It then installs the package, tunes the OS, writes TLS files, renders the config, updates the keystore, syncs plugins and installs GeoIP database files.
   - Nothing restarts a running node in this phase. Changes that need a restart just flag the host.
   - Replacing only PEM certificates or keys doesn't flag a restart, because Elasticsearch reloads them on its own. You can change this with `elasticsearch_restart_on_certificate_change`.
2. **New and stopped nodes** are started in parallel.
   - For a brand-new cluster (no node has data or is running), `cluster.initial_master_nodes` is rendered from the master-eligible hosts in the cluster group.
   - Once the cluster forms, the setting is removed again without a restart.
   - The `elastic` password is seeded from `bootstrap.password`.
3. **Rolling restart of running nodes that need it.** This is driven from the first host in the play:
   - Batches are built with non-master nodes first, then master-eligible nodes, each group with its own size limit.
   - For each batch:
     1. Wait for cluster health.
     2. Set `cluster.routing.allocation.enable: primaries` and run `_flush`.
     3. Stop the nodes in the batch.
     4. If a version change is pending, upgrade the RPM and reinstall plugins.
     5. Start the nodes and wait until they rejoin.
     6. Re-enable allocation and wait for health again.
   - **During a version upgrade,** replicas can't be placed on nodes that are still on the old version. So between batches the role waits for yellow with no initializing or relocating shards. It requires the configured health (normally green) only before the first batch and after the last one.
   - If a batch fails, the play stops and no further nodes are touched. The error message says how to re-enable allocation.
4. **Cluster-level API work,** run once: install the license, manage roles and role mappings, then apply GeoIP downloader settings and manage ingest pipelines.

## Slow and unreliable networks

The role is built to work over high-latency, low-bandwidth links such as satellite. It doesn't rely on SSH pipelining.

**Connection settings.** Put these in your project's `ansible.cfg`; [`examples/ansible.cfg`](examples/ansible.cfg) has them.
- A 60 s connect timeout.
- Keepalives every 30 s, and a connection is only given up after 10 missed replies.
- Compression, and control connections that persist for 30 minutes.
- `retries = 10`, so Ansible reconnects when SSH drops mid-task.
- `gather_timeout = 120`.

**What the role does.**
- **Downloads.** Package, plugin and GeoIP database (`url`) downloads run as async jobs. They keep running on the host if the SSH session drops, and Ansible polls for the result. In `--check` mode they run as normal tasks, because Ansible rejects async tasks in check mode.
  - Failed downloads are retried.
  - The dnf repository gets a longer per-request timeout and more retries: `elasticsearch_repo_timeout` and `elasticsearch_repo_retries` for the Elastic repository, or `elasticsearch_satellite_repo_overrides` for a Satellite repository.
  - dnf waits for a lock held by an interrupted earlier attempt.
  - GeoIP database files are only transferred when their checksum differs. For large databases, prefer `url` from a server close to the nodes over `src` from the control node.
- **Safe retries.**
  - Plugin installs are skipped when the plugin directory already exists, so a retry after a lost response is harmless.
  - API calls that change state (cluster allocation and GeoIP settings, flush, license, roles, role mappings, ingest pipelines) are retried, and all are safe to repeat. DELETE calls accept 404.

The defaults suit ordinary networks. For satellite links, use the values in [`examples/inventory/group_vars/all/network.yml`](examples/inventory/group_vars/all/network.yml):

| Variables | Value |
|---|---|
| `elasticsearch_task_retries`, `elasticsearch_task_delay` | 8 retries, 60 s apart |
| `elasticsearch_download_async_timeout` | 4 hours per download attempt |
| `elasticsearch_api_timeout` | 180 s per request |
| `elasticsearch_api_retries`, `elasticsearch_api_delay` | 180 × 20 s for health and node waits |

A retried task can take up to retries × (delay + attempt time) before it gives up. If many hosts share one link, lower `forks`.

## Firewall

With `elasticsearch_manage_firewall` (on by default) the role opens the HTTP and transport ports in
firewalld, permanently and immediately, in `elasticsearch_firewall_zone`:

| Variable | Default | |
|---|---|---|
| `elasticsearch_manage_firewall` | `true` | Set to `false` to manage the firewall yourself |
| `elasticsearch_firewall_zone` | `public` | Zone the rules go in |
| `elasticsearch_firewall_ports` | `[]` | Anything else this host serves: `["9600/tcp", "9200-9205/tcp"]` |
| `elasticsearch_firewall_services` | `[]` | Named firewalld services |

If firewalld is not running the role says which ports still have to be reachable rather than failing,
so hosts filtering traffic another way are not blocked by it.

## Hardened hosts (FIPS, SELinux, fapolicyd)

The role is meant to run on hosts with the DISA STIG applied. What that takes:

### fapolicyd

**Ansible itself has to be allowed first.** fapolicyd blocks whatever it does not trust, and that
includes the Python module Ansible copies to the host for *every* task. Nothing runs until that is
allowed, so add a rule once, before using these roles:

```
# /etc/fapolicyd/rules.d/30-ansible.rules
allow perm=open exe=/usr/bin/python3.9 : dir=/root/.ansible/tmp/
```

Load it with `fagenrules --load`. The file has to sort before the rules that deny untrusted
interpreted files, and the interpreter and directory have to match the host's Python and the user you
become (`ansible_remote_tmp`). The same rule covers the `repo_lookup.py` helper these roles run in
Satellite mode. Denials are audited, so `ausearch -m fanotify -ts recent` shows what was blocked.

**Plugins.** `elasticsearch-plugin` unpacks jars that rpm never installed, so fapolicyd stops
Elasticsearch loading them. The role adds them to the trust database itself:

| Variable | Default | Purpose |
|---|---|---|
| `elasticsearch_fapolicyd_trust` | `auto` | `auto` acts only when fapolicyd is running; `true` always; `false` never |
| `elasticsearch_fapolicyd_trust_paths` | plugin directory | Paths to trust; directories are added recursively |
| `elasticsearch_fapolicyd_trust_file` | `ansible-elasticsearch` | File under `/etc/fapolicyd/trust.d/` |

Entries are rewritten when a plugin changes and loaded with `fapolicyd-cli --update`. That
includes a rolling upgrade: plugin files carry the Elasticsearch version in their names, so each
node's entries are rewritten after its plugins are reinstalled and before it starts again. With
`elasticsearch_plugins: []` there is nothing to trust and the role leaves fapolicyd alone.

The entries go in a file of their own under `/etc/fapolicyd/trust.d/`, which needs fapolicyd 1.1 or
newer (RHEL 9 and 10 both ship that). Set `elasticsearch_fapolicyd_trust: false` on anything older and manage
trust yourself.

### FIPS mode

**A host running in FIPS mode does not put Elasticsearch in FIPS mode.** Elasticsearch runs the JDK
it ships with, which does not inherit the system policy, so the node keeps running with its usual
crypto. If your reading of the STIG requires FIPS-validated crypto inside the application:

```yaml
elasticsearch_fips_mode: true
elasticsearch_password_hashing_algorithm: pbkdf2_stretch   # bcrypt is not FIPS approved
```

That is only the configuration half. Elasticsearch also needs a FIPS certified security provider
(Bouncy Castle FIPS) added to its JDK and a Platinum, Enterprise or trial license; this role
installs neither, and preflight says so when the setting is on.

**What already suits a FIPS host,** whether or not you turn that on:

- PEM certificates and keys throughout — no PKCS#12 keystores, whose default MAC is not FIPS approved.
- The role's own idempotency hashes are SHA-256.
- **Private keys must not be in the traditional encrypted PEM format.** A key that starts with
  `Proc-Type: 4,ENCRYPTED` derives its key with MD5 and cannot be read at all in FIPS mode. Preflight
  fails with that message when the host reports FIPS. Convert it once:

  ```bash
  openssl pkcs8 -topk8 -in old.key -out new.key
  ```

  Keys issued by [ansible-role-elastic-tls](https://github.com/jmaughmer/ansible-role-elastic-tls)
  are PKCS#8 already.
- **Importing Elastic's GPG key** can fail under a strict crypto policy; use an internal mirror or a
  Satellite repository (see [Package source](#package-source)).

### TLS protocols and ciphers

Elasticsearch already negotiates TLS 1.3 and 1.2 only. To state that explicitly, for evidence:

```yaml
elasticsearch_ssl_supported_protocols: ["TLSv1.3", "TLSv1.2"]
elasticsearch_ssl_cipher_suites: ["TLS_AES_256_GCM_SHA384", "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384"]
```

Both apply to the HTTP and transport layers. Empty keeps Elasticsearch's defaults.

### SELinux

The Elastic packages ship no SELinux policy, so the service runs in `unconfined_service_t` and works
under a targeted policy in enforcing mode. Two things still matter:

- **Relocated directories.** `path.data` and `path.logs` in their packaged locations are labelled
  correctly; moved onto another mount they come out as `default_t`, which a relabel will not fix.
  With `elasticsearch_selinux_manage: auto` the role adds an `fcontext` rule and runs `restorecon`
  for any path you changed, and leaves the packaged ones alone.
- **If your baseline removes unconfined domains,** Elasticsearch needs a local policy module of its
  own. That is outside what this role does.

- **Ansible needs `python3-libselinux`** on the hosts, or the file and copy modules cannot set
  contexts. It is part of a normal RHEL install.
- **`semanage` comes from `policycoreutils-python-utils`,** which is required only when the role
  manages contexts.

## Notes and caveats

- **Adding nodes.** Add the host to the cluster group and run the play against the whole group. The existing nodes' `discovery.seed_hosts` changes when you add a master-eligible node, so they get a rolling restart. Adding data-only nodes changes nothing on the existing nodes.
- **Running with `--limit`** only against brand-new nodes that join an existing cluster: auto-detection can't see the existing data. Set `-e elasticsearch_cluster_bootstrap=false`.
- **Failed first bootstrap.** If a new cluster's first bootstrap fails partway, re-run with `-e elasticsearch_cluster_bootstrap=true`.
- **Single-node clusters** use `discovery.type: single-node`. To grow one into a multi-node cluster, set `elasticsearch_discovery_type: multi-node` before adding nodes.
- **Upgrades** are supported within 9.x. Downgrades and major-version upgrades are refused by preflight.
- **RPM security auto-configuration.** The role removes the keystore entries and the `certs/` directory it creates, because they conflict with the PEM-based TLS config.
- **Check mode** is not supported for first-time installs, because later tasks need the Elasticsearch binaries to exist.
- **Repository GPG key.** If importing Elastic's key fails under your system crypto policy, point `elasticsearch_repo_baseurl` and `elasticsearch_repo_gpgkey` at an internal mirror, or use a Satellite repository (`elasticsearch_repo_source: satellite`).
- **Containers.** `vm.max_map_count` can't be set from inside a container. Set `elasticsearch_manage_sysctl: false` and configure it on the container host instead.
- **Logstash user.** The example `group_vars` create a `logstash_writer` security role for Logstash's Elasticsearch output. The role doesn't create native users; create `logstash_internal` yourself.

## Role layout

```
├── defaults/main.yml          # every tunable, documented
├── vars/main.yml              # internal, lazily evaluated values
├── meta/main.yml
├── files/repo_lookup.py        # reads repository definitions, including redhat.repo
├── tasks/
│   ├── main.yml
│   ├── facts.yml              # installed version, running state, existing data
│   ├── preflight.yml          # validation before anything changes
│   ├── repository.yml              # Elastic repository file, or Satellite repository checks
│   ├── package_version_check.yml   # requested version is available before install/upgrade
│   ├── install.yml
│   ├── system.yml             # sysctl, systemd drop-in, firewalld
│   ├── certificates.yml       # PEM files + key/cert/CA verification
│   ├── configure.yml          # elasticsearch.yml, jvm.options.d
│   ├── keystore.yml           # bootstrap.password, AD bind pw, key passphrase
│   ├── plugins.yml / plugins_sync.yml
│   ├── geoip.yml              # .mmdb database files in ingest-geoip
│   ├── service.yml            # start new/stopped nodes, decide who needs restart
│   ├── rolling_restart.yml / rolling_restart_batch.yml
│   ├── post_start.yml         # wait for cluster, remove initial_master_nodes
│   ├── license.yml
│   ├── security.yml           # roles + role mappings
│   └── ingest.yml             # GeoIP downloader settings + ingest pipelines
├── templates/
└── examples/
```

## Related roles

- [ansible-role-logstash](https://github.com/jmaughmer/ansible-role-logstash): Logstash with multiple pipelines and persisted queues.
- [ansible-role-kibana](https://github.com/jmaughmer/ansible-role-kibana): Kibana connected to this cluster.
- [ansible-role-elastic-tls](https://github.com/jmaughmer/ansible-role-elastic-tls): issues the certificates these roles install, from a private CA or Let's Encrypt.
- [ansible-role-keepalived](https://github.com/jmaughmer/ansible-role-keepalived): virtual addresses in front of the Logstash and Kibana hosts.

All three use the same `ssl_certificates` host variable format.

**Play order for a full stack:** run Elasticsearch first, then Logstash and Kibana. Kibana refuses to start against an older Elasticsearch, and upgrades should go in that order too.

## License

MIT. See [LICENSE](LICENSE).
