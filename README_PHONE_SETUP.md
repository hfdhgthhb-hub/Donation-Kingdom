# Donation Kingdom — phone-only map export and Roblox Model upload

This repository is designed to run on GitHub Actions, so the Rust conversion happens on GitHub's cloud runner. You do not need to install Rust or run a desktop program on your phone.

## Read this before starting

This is an **experimental converter**, not an official Roblox place-to-model converter. Roblox's official Assets API accepts `.rbxm` and `.rbxmx` Model files, but Roblox explicitly warns that model files edited outside Roblox Studio may fail to upload or function. The converter below uses the community `rbx-dom` Rust libraries, not Roblox software. A successful parser round-trip is not proof that Roblox will accept the model or that it will look identical in Studio.

The uploaded source place is about 8.6 MB. GitHub's browser upload limit is sufficient for this file. The converted model must remain below Roblox's 20 MB Create Asset limit; the upload script checks that before sending it.

### Exactly what this exporter includes

- All direct Workspace child trees, copied into one top-level `Model` without changing their part transforms, sizes, colors, or materials.
- References between objects in those copied Workspace trees are remapped together.
- MeshParts, SurfaceAppearance instances, Decals, and Texture instances are kept. The dependency report records common mesh/texture properties.
- Script, LocalScript, ModuleScript, CoreScript, common GUI classes, ProximityPrompt, Dialog, DialogChoice, Camera, Terrain, and TerrainRegion instances are removed.

### What it does NOT preserve

- **Terrain and TerrainRegion are omitted.** If the map uses Roblox Terrain, those parts of the map will be missing from this Model.
- **Lighting and other services are omitted.** Sky, Atmosphere, post effects, Lighting settings, SoundService, ReplicatedStorage, ServerStorage, StarterGui, and other non-Workspace services are not copied.
- Objects that a game script normally clones into Workspace from ReplicatedStorage or ServerStorage will not be present unless they were already saved in Workspace in the source file.
- Mesh/texture/PBR asset contents are not downloaded or re-uploaded. Their asset references are kept. The destination experience still needs permission to use each referenced asset; restricted assets from another creator may not display.
- References from Workspace objects to objects outside the copied Workspace trees may be cleared.
- Runtime behavior is not copied because scripts are intentionally removed.

Only use this workflow for a map you own or have permission to extract, upload, and redistribute. A free-to-play game is not automatically free to re-upload as your own model.

---

## Part A — Create a private GitHub repository (phone browser)

1. Download the ZIP containing this project.
2. Open [GitHub](https://github.com/) in your phone browser and sign in.
3. Create a **Private** repository. Do not make it public: you will upload the source `.rbxl` file and the repository contains the exporter code.
4. Extract the ZIP on your phone. Open the `DonationKingdomCloudExporter` folder and upload its **contents** into the repository root, preserving the subfolders (`.github/workflows`, `src`, `scripts`, and `tests`). The ZIP already contains `Donation Kingdom.rbxl` beside `Cargo.toml`; upload that file exactly once.
5. Commit the files to the repository's default branch.

The original upload is 8,616,600 bytes, so it is below GitHub's 25 MiB browser-upload limit.

## Part B — Create a Roblox Open Cloud API key

1. Open [Roblox Creator Dashboard → Credentials](https://create.roblox.com/dashboard/credentials).
2. Create an API key. Give it a clear name such as `Donation Kingdom Model Upload`.
3. In the key's Access Permissions, enable **Assets** and grant the read/write operations required by the Assets API. Roblox currently documents adding Assets access and Read/Write operation permissions for the selected creator/resource.
4. Copy the key once and keep it private. Never put it in a source file, a public repository, a screenshot, or a chat message.
5. In GitHub, open your private repository → **Settings → Secrets and variables → Actions → New repository secret**.
6. Name the secret exactly `ROBLOX_API_KEY` and paste the key as its value.

If the destination experience is owned by a group, the API key must be allowed to create assets for that group and the group must have the relevant permissions. If the experience is personally owned, use your own user ID instead.

## Part C — Run the cloud conversion and upload

1. In GitHub, open the repository's **Actions** tab.
2. Select **Export map-only RBXM and upload Roblox Model**.
3. Press **Run workflow**.
4. Choose `user` if the asset should belong to your personal Roblox account. Choose `group` if it should belong to the same group that owns your destination experience.
5. Enter the matching numeric Roblox User ID or Group ID. Do not enter a username.
6. Keep the asset name as `Donation Kingdom Map` or enter another name.
7. Press **Run workflow**.
8. Open the running workflow and watch the steps. It will:
   - compile the Rust converter on GitHub's runner;
   - parse the `.rbxl` file;
   - copy Workspace trees into a Model;
   - remove scripts, UI, cameras and terrain;
   - write a binary `.rbxm`;
   - reopen the `.rbxm` and validate its Model root and exclusions;
   - run the Python upload-helper tests;
   - call Roblox's Create Asset API once and poll the operation for the Asset ID.
9. When the run finishes, open **Artifacts** and download `DonationKingdom-Map-Output`. It contains the `.rbxm`, `ExportReport.txt`, `Dependencies.txt`, and (only if Roblox upload succeeded) `asset_id.txt`.

The workflow currently uses the v7 releases of GitHub's checkout, Python setup, and artifact-upload actions, with the stable Rust toolchain.

If the workflow fails during conversion, open the failed log and read the first `ERROR:` line. If conversion succeeds but Roblox rejects the `.rbxm`, download the artifact anyway and read `ExportReport.txt`; Roblox's warning about externally edited model files may be the reason. Do not keep re-running the upload step blindly: an interrupted request can be ambiguous, and duplicate packages are possible.

## Part D — Find and use the Model

- A successful Assets API upload creates a Roblox Model package. It is not automatically public on the Creator Store.
- Find it in Creator Hub under **Development Items** / **Models**, or in Studio's Toolbox under **My Packages**. Roblox documents that uploaded Models are packages.
- If you want it publicly listed, open the asset's Configure page, review its name/description, and enable **Distribute on Creator Store** under Distribution, then save. Do this only if you own or have redistribution rights for the map and all restricted dependencies. Public distribution can be blocked by restricted dependencies owned by other creators.
- If you only need it in an experience, make sure the Model creator matches the experience creator (user vs group) or that the asset owner has shared it with the experience creator. `InsertService:LoadAsset()` is limited to assets owned/shared by the experience creator. `AssetService:LoadAssetAsync()` is the modern loading API; public third-party free assets require the experience's **Allow Loading Third Party Assets** setting.
- Loading from a script happens at runtime; it does not permanently insert the Model into Studio Lite's editable Workspace. A Creator Store/Toolbox insert or an editor-side insertion is needed for a permanent edit.

## API details used by this project

- Create Model endpoint: `POST https://apis.roblox.com/assets/v1/assets` (Roblox currently marks the Assets API endpoints Beta).
- Model format: `.rbxm`, MIME type `model/x-rbxm`.
- Multipart fields: `request` (JSON metadata) and `fileContent` (binary model).
- Operation polling: `GET https://apis.roblox.com/assets/v1/operations/{operationId}`.
- Asset ID is read from `response.assetId` (with `response.path` as a fallback).
- Current official Create Asset content limit: 20 MB per upload. The uploader checks the generated `.rbxm` size before sending it.

These are the currently documented Assets API paths. The endpoints are marked Beta in Roblox's API index and may change. Each Create Asset upload supports up to 20 MB.

## Local test status

The Python upload helper has 8 offline unit tests. These tests use a fake HTTP session and do not contact Roblox or expose a real API key. The Rust converter is configured to compile and run in GitHub Actions; this chat environment does not have Cargo/Rust installed, so the Rust build and actual parsing of your `.rbxl` have **not** been run here. The workflow performs a real build, conversion, binary re-open check, and filter validation when you run it on GitHub. No one can promise the Roblox side will accept the externally generated `.rbxm` because Roblox documents that risk.
