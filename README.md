# Aristoma Public Slides

Source and version history for the public Slides collection. This repository is public; only material cleared for public sharing belongs here.

## Layout

```text
site/
  index.html
  <deck-slug>/
    index.html
    assets/
scripts/
  validate_site.py
.github/workflows/
  publish.yml
```

Keep each presentation under a stable `site/<deck-slug>/` path and add its link to the collection index. Commit the complete approved site so publishing a new deck does not remove older presentations. Only `site/` is uploaded to Azure; repository documentation and validation tools are not website content.

## Validate Locally

```text
python -m pip install -r requirements-dev.txt
python -B scripts/validate_site.py --self-test
python -B scripts/validate_site.py --workflow .github/workflows/publish.yml
python -B scripts/validate_site.py site
```

The site check uses Python's standard library. Structured workflow validation uses the pinned PyYAML development dependency. Browser-side presentation rendering does not depend on these Python tools.

Checks cover allowed file types, private configuration, symlinks, UTF-8/JSON metadata, HTML resource references, local fragments/slide hashes, path traversal, and selected credential patterns. They are not an exhaustive secret scan or content clearance. CSS URLs, external availability, rendered layouts, speaker notes, hidden slides, assets, and public-readability still need human/browser review.

The initial collection page is empty. Its font and avatar are public external resources; it is not an offline-ready deck or a published presentation.

## Publish

The `Publish public slides` workflow runs only through `workflow_dispatch`. It requires the `main` branch, the `aristo7298sub` actor, this exact repository, and the confirmation value `PUBLISH`. A commit or push does not publish automatically.

The workflow validates the complete site, then uploads only `site/` to the existing Free Azure Static Web App. It does not build an API, create Azure resources, register a domain, or change DNS. The `AZURE_STATIC_WEB_APPS_API_TOKEN` Actions Secret must belong to that approved site; never place its value in a file, command argument, or chat.

Source updates and production publication require separate review. During initial setup, the workflow is prepared but not triggered; the live endpoint remains Azure's platform placeholder until a separately approved release.

The default endpoint is `https://yellow-desert-0fac5eb00.5.azurestaticapps.net/`. Domain registration and binding are separate work and have not been completed.

## Dependency Versions

The workflow pins `actions/checkout` v6, `actions/setup-python` v6, and `Azure/static-web-apps-deploy` v1 to the public tag commit SHAs verified during setup. Validation uses Python 3.12 in CI. Review upstream changes before updating the pinned actions or development dependency.
