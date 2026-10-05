# package_hold_prerequisites

Run from the project root with its `ansible.cfg`; keep `library/`, `module_utils/`,
`filter_plugins/` and the exporter source alongside `roles/`. No collection installation.

Audit probes native executables, packages and configuration. Remediation installs only missing prerequisites and probes again. Disabled plugins remain configuration errors.

Tested controller environment: ansible-core 2.13.13, Python 3.8.20.
Newer Ansible versions are not blocked, but have not been tested separately.

Shared inventory, privileges and full examples: [README](../../README.md).

| Variable | Default |
|---|---|
| `package_hold_packages` | required inventory; no default |
| `package_hold_prerequisites_mode` | `audit` |
| `package_hold_prerequisites_command_timeout_seconds` | `30` |
| `package_hold_prerequisites_dnf_plugin_package` | `python3-dnf-plugin-versionlock` |

Tags: `pre-req, config, never/clean`.

```yaml
- hosts: package_hold_targets
  become: true
  vars:
    package_hold_packages:
      - id: bash
        names: {redos: bash, astra: bash, alt: bash}
  roles:
    - package_hold_prerequisites
```

Molecule uses Docker and a native systemd image via `MOLECULE_IMAGE`.
Exporter checks installed bash and an absent name, native scanner under systemd
hardening, cache/HTTP/idempotence/removal; prerequisites/control
need native prerequisites or a reachable repository. Solver/MAC require VM acceptance.
Run clean in a dedicated play because it ends that play.
