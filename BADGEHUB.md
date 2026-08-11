# BadgeHub submission info: ZapTV and BlockTV

Everything needed to create both projects at
[badgehub.eu/page/create-project](https://badgehub.eu/page/create-project).
Neither app is on BadgeHub yet (checked against the 49 projects currently
carrying the `mpos_api_0` badge).

Upload files are staged at `/Users/RT/Developer/dev-zaptv/dist/`:

- `org.zaptv.app_0.3.1.mpk` and `org.zaptv.app_0.3.1_64x64.png`
- `org.zaptv.blocktv_0.2.0.mpk` and `org.zaptv.blocktv_0.2.0_64x64.png`

## How BadgeHub works

Each project has a **slug** (its permanent URL id), and each published
**revision** carries a `metadata.json` plus the project files. The MPOS
app store reads BadgeHub through the `mpos_api_0` badge, so that badge must
be selected or the app will not appear on devices.

The slug must equal the app's `fullname` in MANIFEST.JSON, because that is
what MicroPythonOS installs the app as.

Upload limit is 100 MB per file. Both packages are well under.

Two description fields exist, and BadgeHub names them differently from
MANIFEST.JSON. BadgeHub's `description` is the one-line summary shown on
cards and app lists (MANIFEST calls it `short_description`), while
`long_description` is the full paragraph on the project page. Both are
given per app below.

## ZapTV

| Field | Value |
|---|---|
| Slug | `org.zaptv.app` |
| Name | ZapTV |
| Author | ZapTV |
| Short description (`description`) | Nostr-native zap display for your npub. |
| Categories | Finance, Data |
| Badge | `mpos_api_0` |
| Version | 0.3.1 |
| Development status | stable |
| Git URL | https://github.com/bitcoin3us/ZapTV |
| License | MIT |

**Long description (`long_description`)**

> Connect an LNbits, Nostr Wallet Connect, or on-chain (xpub) wallet and ZapTV shows your most recent zaps with a lightning-strike effect, alongside your Nostr profile picture and a receive QR for your npub.cash Lightning address. Display-only: it holds no keys and shows no balance, so it is safe to leave running in public.

**Files to upload**

- `org.zaptv.app_0.3.1.mpk` (195 KB)
- `icon_64x64.png` (64x64 RGBA, transparent corners)
- `metadata.json` (prepared, see below)

## BlockTV

| Field | Value |
|---|---|
| Slug | `org.zaptv.blocktv` |
| Name | BlockTV |
| Author | ZapTV |
| Short description (`description`) | Customisable Bitcoin dashboard: block height, price, moscow time, fees, halving, charts and zaps. |
| Categories | Finance, Data |
| Badge | `mpos_api_0` |
| Version | 0.2.0 |
| Development status | stable |
| Git URL | (no public repo yet) |
| License | MIT |

**Long description (`long_description`)**

> BlockTV is a clean, customisable Bitcoin dashboard. Compose your own screens from seventeen data fields: block height, spot price, moscow time, halving countdown, three fee rates, circulating supply, market cap, clock, price charts over 24h/7d/30d/1y and a full four-year halving cycle, latest nostr zap and NWC wallet balance. Swipe between screens, roll the numbers odometer-style, and pick your own text and background colors. Every chart point is a real market sample, and stale data is coloured honestly rather than hidden.

**Files to upload**

- `org.zaptv.blocktv_0.2.0.mpk` (229 KB)
- `icon_64x64.png`
- `metadata.json` (prepared, see below)

## Where metadata.json goes

There is no JSON box on the create page. That page has exactly one field,
**Slug**, and on submit it sends you to the project editor at
`/page/project/<slug>/edit`. Everything else happens there.

Slug rules: `^[a-z][.a-z_0-9-]{2,100}$`, so it must start with a lowercase
letter and may then contain lowercase letters, digits, dots, underscores,
and hyphens. Both `org.zaptv.app` and `org.zaptv.blocktv` pass.

On the editor page there are two equivalent ways to supply the metadata:

1. **Fill the form** (the normal path). The edit form's fields *are*
   metadata.json: App Name, Author, Git URL, Version, Short Description,
   Long Description, License Type, and categories. BadgeHub writes the file
   for you, and it then shows up in the "Project files" list as a protected
   entry that cannot be deleted.
2. **Upload a file literally named `metadata.json`** in the Project files
   section. The backend intercepts that filename, validates it against the
   schema, and applies it as the draft metadata instead of storing it as a
   plain file. Invalid JSON or a schema mismatch is rejected with a
   specific error rather than silently accepted.

Either way, upload the `.mpk` and the icon as ordinary files in the same
Project files section, then publish the revision.

**The API Token section is optional.** The website authenticates you through
your login (Keycloak), so uploading and publishing in the browser needs no
token. A per-project token exists only for scripted access to the same REST
API, sent as a `badgehub-api-token` header:

```
curl -H "badgehub-api-token: YOUR_PROJECT_TOKEN" https://badgehub.eu/api/v3/projects/org.zaptv.app/draft
```

Worth generating later if you want releases pushed from CI; skip it for the
first manual upload. Tokens can be revoked and regenerated at any time.

The `executable` must match the uploaded .mpk filename exactly.

```json
{
    "project_type": "app",
    "git_url": "https://github.com/bitcoin3us/ZapTV",
    "development_status": "stable",
    "name": "ZapTV",
    "description": "Nostr-native zap display for your npub.",
    "long_description": "...",
    "categories": ["Finance", "Data"],
    "author": "ZapTV",
    "icon_map": { "64x64": "icon_64x64.png" },
    "version": "0.3.1",
    "badges": ["mpos_api_0"],
    "application": [
        { "type": "micropython", "executable": "org.zaptv.app_0.3.1.mpk" }
    ]
}
```

## Reference values from the platform

- **Badge slugs**: `mpos_api_0` (this is the MicroPythonOS one), `mch2022`,
  `tanmatsu`, `cz20`, `brucon_0x10`, `fri3d_2026`, `fri3d_2024`, `fri3d_2022`
- **Categories**: Audio, Communication, Data, Development, Driver,
  Event-related, **Finance**, Game, Graphics, Hacking, Hardware, Interpreter,
  Knowledge, Network, SAO, Silly, System, Troll, Uncategorised, Utility,
  Virus, Wearable, Adult
- **Development status**: `stable` (48 of 49 MPOS projects) or
  `work_in_progress`
- **Max upload size**: 100 MB per file

## Before you publish

1. **BlockTV's MANIFEST.JSON long_description is stale.** It says "ten data
   fields" but the code defines seventeen. Worth fixing in the manifest so
   the on-device store text matches BadgeHub.
2. **BlockTV still uses the deprecated nested layout** (`assets/blocktv.py`,
   entrypoint `assets/blocktv.py`). It loads fine but logs deprecation
   warnings at boot. Migrating to the flat layout would also mean bumping to
   0.2.1 and rebuilding the .mpk.
3. **BlockTV has no public repo yet**, so its `git_url` is blank. Creating
   one (as with ZapTV) would let BadgeHub link the source.
4. The `.mpk` files were built with the same recipe as MPOS's
   `bundle_apps.sh`: fixed modification times, sorted file order, stored
   (uncompressed) zip, so they are byte-reproducible.
