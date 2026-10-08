# Google Cloud deployment templates

These are reviewable Cloud Run templates, not deployed infrastructure and not a
complete production platform. They intentionally disable demo login and all live
tools. Read `../../docs/deployment.md` before applying them.

The templates assume existing Artifact Registry, Cloud SQL PostgreSQL 16, database
roles, Secret Manager secret versions and service accounts. They do not create
networks, databases, credentials, IAM bindings or Redis. Terraform should provision
those resources only after project, region, budget, retention and identity decisions
are made. No speculative Terraform state or claimed successful cloud plan is shipped.

API and worker use the same image. The API may scale to zero; the database-polling
worker must keep one instance with CPU always allocated. API/worker service accounts
need `roles/cloudsql.client` and secret-version access only to their relevant
secrets. The migration account uses a separate database role with schema ownership.
The web service account needs neither SQL nor secret access. Do not grant public
invocation to the worker or migration job.

`render.py` fills only resource identifiers, origins, immutable image references and
secret version numbers. Secret contents are never template inputs. Render into
ignored `work/`, review the result, and apply only within a user-authorized cloud
deployment session. Current verification is YAML parsing and placeholder validation;
Cloud Run server-side validation has not been run against a real project.
