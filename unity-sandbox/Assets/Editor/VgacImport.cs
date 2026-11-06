// Batch-mode validation importer for Video Game Asset Creator.
// Run: Unity.exe -batchmode -nographics -quit -projectPath <sandbox> -executeMethod VgacImport.Run -vgacInput <dir> -vgacName <name>
//      [-vgacHumanoid] [-vgacClips <library.fbx>] [-vgacSpeedScale <f>] [-vgacKeepPose]
//      [-vgacHitboxes <hitboxes.json>] [-vgacMovementShape auto|upright|along|box|none] [-vgacScale <f>] [-vgacClipInfo <anim_clips.json>]
//      [-vgacCategory <category>] [-vgacColliders <colliders.json>]
//      [-vgacFilter bilinear|point] [-vgacMipmaps on|off] [-vgacCompress on|off] [-vgacNormalMap on|off]
//      [-vgacSockets <sockets.json>] [-vgacAttachments <attachments.json>] [-vgacHair on] [-vgacHairShader vgac|lit]
//      [-vgacSprings <springs.json>] [-vgacHairCutout on] [-vgacHairTwoSided on]
// Expects in <dir>: <name>.fbx (meshes named <name>_LOD0..n, optional collision meshes <name>_collider_00.._nn or
// <name>_collider_mesh (older files: <name>_collider), optional armature), textures
// base_color.png, normal.png, metallic.png, roughness.png, ao.png and T_<name>_MetallicSmoothness.png (any subset).
// Writes <dir>/unity_report.json.
//
// Hand-off to an open Editor (Version 6): Unity will not open a project in batch mode while the Editor has it open, so
// the backend then copies the same inputs into Assets/VGAC/<name>/_incoming/ with a request file vgac_import.json
// (request_id, importer_version, args: the batch arguments). VgacImportWatcher below picks it up inside the open
// Editor (after an asset import and once at load), runs the same Import routine, writes unity_report.json next to the
// request with the request id and renames the request to vgac_import.done.json. Menu: Tools/VGAC/Import pending assets.
// Batch check of the same sweep: -executeMethod VgacImport.ImportPending.
//
// Recipes (Version 7): the style's engine settings. -vgacFilter point (crisp pixel looks) sets every texture to Point
// filtering, -vgacMipmaps off and -vgacCompress off keep the small textures exact; -vgacNormalMap off (looks without
// surface bumps) leaves the material's normal slot empty and removes a normal.png an earlier import of the same name
// left in Assets/VGAC/<name>. The report records textureFilter, textureMipmaps, textureCompression and normalMap.
// Without these arguments the importer keeps Unity's defaults (bilinear, mipmaps, compression) as before.
//
// Sockets (Version 8): a rigged character's FBX carries attachment points, empties named Socket_Hand_R, Socket_Back
// and so on, parented to their bones (backend/vgac/sockets.py). They arrive as plain child transforms under the bones
// and stay in the prefab, with nothing visible added. With -vgacSockets the importer checks each one against
// sockets.json (the right bone, and its position and axes, glTF model space like hitboxes.json), records how far the
// FBX's own placement was off (socketMaxError in metres, socketMaxAngle in degrees), then sets the file's placement
// exactly: forward +Z and up +Y as the file gives them. A socket the FBX lacks is added from the file. The Humanoid
// mapping never treats a socket as a bone. The report lists the sockets found in the saved prefab (sockets) and
// their bones (socketBones, "Socket_Hand_R>mixamorig:RightHand").
// Version 11: humanoids also carry Socket_Forearm_L and Socket_Forearm_R (the outside of each forearm, forward out of
// the arm, up toward the elbow), placed from sockets.json like the others; a shield's attachments.json entry names
// Socket_Forearm_L, so the equipped prefab hangs it under that point. Item offsets in attachments.json already
// include the backend's fit to the motion ("Fit it for me"), so nothing here changes them.
// Version 12: every texture imports at its own size (Unity's default import limit is 2048, so a 4096 base colour used
// to arrive at 2048); the report lists textureSizes ("base_color.png:4096:4096": the file's size, the import's limit).
// Version 13: hair as its own material. With -vgacHair on the input also holds hair_base_color.png, hair_normal.png,
// hair_ao.png, hair_flow.png and T_<name>_Hair_MetallicSmoothness.png, and the FBX's meshes have two material slots
// (<name>_Body, <name>_Hair; the hair is the second sub-mesh). A second material, <name>_Hair.mat, is built from the hair
// textures: on the built-in render pipeline with the "VGAC/Hair" shader (Assets/VgacShaders/VgacHair.shader, a two-band
// Kajiya-Kay highlight along the strands from the flow map), elsewhere, or with -vgacHairShader lit, or when that
// shader is missing, with the same Lit or Standard shader as the body. Every renderer with two sub-meshes gets both
// materials. The report gives hairMaterial, hairShader, hairSubmeshes (renderers with the hair sub-mesh),
// hairTriangles (LOD0's hair sub-mesh) and hairTexturesAssigned. Snapshot also renders the head close up under two
// light directions with the hair shader and with the body's shader (hair_*.png) when the prefab has a hair material.
// Version 14: the primary hair highlight takes half the hair's own colour (it washed the crown out in white).
// Version 15: spring bones. With -vgacSprings the input holds springs.json (backend/vgac/blender/springs.py): chains
// of bones named Spring_* that hang from a body bone (a ponytail, a cape, a tail). The prefab gets VgacSpringBones
// (Assets/VgacScripts/VgacSpringBones.cs, found by name, so a project without it still compiles and only warns), set up
// while the instance stands in its bind pose; it sways the chains during the shared Humanoid clips, which never move
// them. The shape-based Humanoid mapping never treats a spring bone as a body bone. The report gives springChains,
// springBones and springWarning. Snapshot also renders walk and run from the side with the script stepping frame by
// frame, and the same frames with it off (springs_*.png), and lists how far each chain tip trails (in loopProbes).
// Version 16: moves face forward. The shared library (clips.fbx) imports with Bake Axis Conversion on, as its author's
// setup sheet asks: without it the library's rig carries a half turn, and every looping move whose root rotation is
// baked into the pose "based upon original" played facing backwards (measured 125 to 180 degrees; one-off moves, whose
// root turn is thrown away, faced forward). After the loops are set, every Humanoid move is played on a copy of the
// model and the body's heading measured (the hips and shoulders, averaged over the move); a baked loop more than 10
// degrees off is tried "based upon body orientation" instead and keeps whichever faces closer to forward. The report
// gives clipFacing (per move: degrees, how it is based, whether the controller uses it) and bindFacing (the model's own
// stance unanimated). A project imported by an older importer is brought up to date once: RefreshClipFacing re-imports
// every Assets/VGAC/*/clips.fbx that lacks the mark (userData "vgac-facing-16-2") the same way, at the next batch import,
// hand-off sweep or Editor load (Tools/VGAC/Fix move facing). Snapshot evaluates with culling off, so the first frame
// shows the real pose rather than the bind pose.
// Version 17: hair from the library (docs/HAIR-LIBRARY-PLAN.md). With -vgacHairCutout on (hair cards: flat strips with a
// see-through texture) or -vgacHairTwoSided on, the hair material is built on "VGAC/HairCutout"
// (Assets/VgacShaders/VgacHairCutout.shader: the same two-band lighting as VGAC/Hair, alpha cutout, both sides drawn with
// the normal turned round on back faces, cut-out shadows) with _Cutoff 0.5 and _Cull 0 (two-sided) or 2. The hair's
// colour texture then keeps its alpha (alpha from input, alpha is transparency) and its mipmaps preserve coverage at
// alpha 0.5, so cards do not thin out at a distance. URP and HDRP fall back to their Lit shader with alpha clipping
// (not verified). The report gives hairCutout and hairTwoSided. Without the two arguments nothing changes: VGAC/Hair
// stays the hair shader and VgacHair.shader is not touched. Snapshot with -vgacHeadPitch on (diagnostics, never passed by
// the export) also lists in loopProbes how far idle, walk and run bow the head from its bind pose.
//
// Version: raise it with every change. The backend (stages/export.py) copies this file into a user's Unity project
// when the copy there is missing or has a lower Version (docs/GREAT-ASSETS-PLAN.md 2.6).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.Rendering;

public static class VgacImport
{
    public const string Version = "17";
    public const string HairShaderName = "VGAC/Hair";
    public const string HairCutoutShaderName = "VGAC/HairCutout";

    [Serializable]
    class ClipInfo
    {
        public string name = "";
        public bool loop;          // Loop Time as imported (read back from the clip)
        public bool defaultLoop;   // Loop Time Unity chose by itself before this script set it
        public bool bakedInPlace;  // root rotation, height and XZ baked into the pose
    }

    // Version 16: which way the body faces while a move plays, against the prefab's forward.
    [Serializable]
    class ClipFacing
    {
        public string name = "";
        public string source = "";     // shared (the clip library) | own (the model's FBX)
        public bool inController;      // the prefab's controller plays it
        public bool measured;          // false when the body lay sideways in too many samples to tell
        public float degrees;          // circular mean of the heading over the move, signed about +Y (+ is to the right)
        public float swing;            // widest swing either side of that mean
        public string basedOn = "";    // original | body for a baked loop; "" when the root rotation is not baked
        public float lean;             // degrees the hips-to-head line leans from upright, averaged (a broken retarget bends it)
    }

    [Serializable]
    class ControllerInfo
    {
        public string path = "";
        public List<string> states = new List<string>();
        public List<string> parameters = new List<string>();
        public List<string> locomotion = new List<string>();  // "clip@speed" for each blend tree child
        public List<string> roles = new List<string>();       // "Attack=Rig|Sword_Attack" and so on
        public int moreMoves;
        public List<string> added = new List<string>();       // "SwordBlock:Trigger", "SitIdle:Bool": moves the person added
        public bool applyRootMotion;
    }

    [Serializable]
    class Report
    {
        public bool ok;
        public string error = "";
        public string importer_version = Version;
        public string request_id = "";         // set for an Editor hand-off request; "" for a batch import
        public string prefab = "";
        public string showcase = "";          // the showcase scene the asset now stands in ("" when not updated)
        public int lodCount;
        public List<int> lodTriangles = new List<int>();
        public int bones;
        public string avatar = "";
        public bool humanoidValid;
        public string avatar_mapping = "";     // explicit | auto | generic | none
        public string avatar_mapping_by = "";  // names | shape (explicit only)
        public int humanBonesMapped;
        public bool tPoseApplied;
        public int colliderVertices;
        public string shader = "";
        public List<string> texturesAssigned = new List<string>();
        public List<string> textureSettings = new List<string>();
        public List<string> textureSizes = new List<string>();     // "base_color.png:4096:4096" file size, then the import's max size
        public List<string> warnings = new List<string>();
        public int clipCount;
        public List<ClipInfo> clips = new List<ClipInfo>();
        public ControllerInfo controller = new ControllerInfo();
        public bool clipInfoUsed;              // loop flags came from anim_clips.json, not from the name rule
        public int hitboxCount;
        public string movementShape = "";      // upright | along | box | none; "" when no hit-box file was given
        public float hitboxMaxBoneError;       // metres: worst bone-position mismatch between the file and the prefab
        public int colliderCount;              // collision components under the Colliders child (G16)
        public List<string> colliderKinds = new List<string>();
        public int colliderTrianglesMax;       // most triangles in one convex MeshCollider (Unity's cap is 255)
        public float colliderBoundsError;      // metres between the shapes' combined box and the LOD0 renderer's box
        public bool secondaryUV;               // lightmap UVs generated on import (G17)
        public bool secondaryUVOnMesh;         // LOD0's mesh really has a UV1 channel after import
        public string secondaryUVSettings = "";
        public List<string> staticFlags = new List<string>();
        public string lodFadeMode = "";        // G20: CrossFade for foliage
        public float lodFadeWidth;
        public string textureFilter = "";      // recipes: Bilinear or Point, as set on the textures
        public bool textureMipmaps = true;
        public string textureCompression = "";
        public bool normalMap = true;          // false when the look has no surface bumps
        public List<string> sockets = new List<string>();      // Socket_* transforms in the saved prefab
        public List<string> socketBones = new List<string>();  // "Socket_Hand_R>mixamorig:RightHand"
        public float socketMaxError;           // metres: worst gap between the FBX's socket and sockets.json
        public float bindPoseMoved;            // metres: how far the model's default pose was from its bind pose
        public float socketMaxAngle;           // degrees: worst forward or up mismatch between the FBX and the file
        public int socketsAdded;               // sockets the FBX lacked, added from the file
        public string hairMaterial = "";       // Version 13: the hair's own material ("" when there is none)
        public string hairShader = "";
        public int hairSubmeshes;              // renderers that got the hair material on their second sub-mesh
        public int hairTriangles;              // LOD0's hair sub-mesh
        public List<string> hairTexturesAssigned = new List<string>();
        public bool hairCutout;                // Version 17: hair from the library, cut out (VGAC/HairCutout)
        public bool hairTwoSided;              // Version 17: drawn from both sides
        public List<string> lod0Materials = new List<string>();   // LOD0's materials in the saved prefab
        public string equippedPrefab = "";     // items: "<Name> (equipped).prefab", a variant with the items on
        public List<string> equippedItems = new List<string>();  // "Ranger - sword>Socket_Hand_R"
        public int springChains;               // Version 15: chains set up on the prefab's VgacSpringBones
        public int springBones;
        public string springWarning = "";
        public List<ClipFacing> clipFacing = new List<ClipFacing>();  // Version 16: per Humanoid move
        public bool facingMeasured;            // false for Generic rigs and when the measuring copy could not be made
        public float bindFacing;               // degrees: the model's own stance, unanimated
    }

    // attachments.json (backend/vgac/items.py equip): per item, its socket and offset in the socket's glTF frame.
    [Serializable]
    class AttachFiles { public string fbx = "", glb = ""; }

    [Serializable]
    class AttachItem
    {
        public string item = "", export_name = "", label = "", socket = "", unity_prefab = "";
        public float[] position = new float[3], rotation = new float[] { 0, 0, 0, 1 };
        public float scale = 1f;
        public AttachFiles files = new AttachFiles();
    }

    [Serializable]
    class AttachDoc { public List<AttachItem> items = new List<AttachItem>(); }

    // sockets.json (backend/vgac/sockets.py to_json): glTF model space, Y up, metres; forward +Z, up +Y.
    [Serializable]
    class SocketItem
    {
        public string name = "", label = "", bone = "";
        public float[] position = new float[3], forward = new float[3], up = new float[3];
    }

    [Serializable]
    class SocketDoc
    {
        public List<SocketItem> sockets = new List<SocketItem>();
    }

    static bool IsSocketName(string n) => n.StartsWith("Socket_", StringComparison.Ordinal);
    static bool IsSpringName(string n) => StripName(n).StartsWith("Spring_", StringComparison.Ordinal);

    // colliders.json (backend/vgac/stages/export.py _write_colliders): the kind of each collision part.
    [Serializable]
    class ColPart
    {
        public string name = "", kind = "";
        public int triangles, vertices;
    }

    [Serializable]
    class ColDoc
    {
        public string chosen = "";
        public List<ColPart> parts = new List<ColPart>();
    }

    // G17: categories that get lightmap UVs (environment also gets static flags); G20: categories that cross-fade.
    static readonly string[] LightmapCategories = { "environment", "prop_rigid" };
    static readonly string[] CrossFadeCategories = { "foliage" };
    const float CrossFadeWidth = 0.25f;

    static bool IsColliderName(string n) => n.Contains("_collider");

    // hitboxes.json (backend/vgac/blender/hitboxes.py to_json): glTF model space, Y up, metres.
    [Serializable]
    class HitShape
    {
        public string name = "", region = "", label = "", bone = "", kind = "";
        public float[] center = new float[3], axis = new float[3], forward = new float[3], size = new float[3];
        public float[] bone_head = new float[3], offset = new float[3];
        public float radius, height;
        public int direction = 1;
    }

    [Serializable]
    class HitMovement
    {
        public string @default = "";
        public HitShape upright, along, box;
    }

    [Serializable]
    class HitDoc
    {
        public List<HitShape> hitboxes = new List<HitShape>();
        public HitMovement movement;
    }

    static string Arg(string key)
    {
        var a = Environment.GetCommandLineArgs();
        for (int i = 0; i < a.Length - 1; i++) if (a[i] == key) return a[i + 1];
        return null;
    }

    static bool Flag(string key) => Environment.GetCommandLineArgs().Contains(key);

    // Everything VgacImport.Run reads from its command line. The batch entry point and the Editor hand-off both build
    // one of these and call Import, so the two paths cannot drift apart.
    public class ImportArgs
    {
        public string input, name, category = "", hitboxes, movementShape, clipInfo, clips, colliders, sockets, attachments, springs;
        public bool humanoid, keepPose;
        public string filter = "bilinear";     // recipes: bilinear | point
        public bool mipmaps = true, compress = true, normalMap = true;
        public bool hair;                      // -vgacHair on: the hair's own material (Version 13)
        public string hairShader = "vgac";     // vgac | lit
        public bool hairCutout;                // -vgacHairCutout on: alpha cutout hair (Version 17)
        public bool hairTwoSided;              // -vgacHairTwoSided on: two-sided hair (Version 17)
        public float speedScale = 1f, scale = 1f;
        public string requestId = "";

        // argv: "-vgacName", "X", "-vgacHumanoid", ... (the batch call's own arguments). baseDir, when given, is where
        // relative file arguments are resolved (the request's folder); the batch path passes null.
        public static ImportArgs Parse(IList<string> argv, string baseDir)
        {
            string A(string key)
            {
                for (int i = 0; i < argv.Count - 1; i++) if (argv[i] == key) return argv[i + 1];
                return null;
            }
            string F(string key)
            {
                var v = A(key);
                if (v == null || baseDir == null || Path.IsPathRooted(v)) return v;
                return Path.GetFullPath(Path.Combine(baseDir, v));
            }
            var a = new ImportArgs
            {
                input = baseDir != null ? Path.GetFullPath(baseDir) : A("-vgacInput"),
                name = A("-vgacName"),
                humanoid = argv.Contains("-vgacHumanoid"),
                keepPose = argv.Contains("-vgacKeepPose"),
                category = A("-vgacCategory") ?? "",
                hitboxes = F("-vgacHitboxes"),
                movementShape = A("-vgacMovementShape"),
                clipInfo = F("-vgacClipInfo"),
                clips = F("-vgacClips"),
                colliders = F("-vgacColliders"),
                sockets = F("-vgacSockets"),
                attachments = F("-vgacAttachments"),
                springs = F("-vgacSprings"),
                scale = ParseFloat(A("-vgacScale"), 1f),
                speedScale = ParseFloat(A("-vgacSpeedScale"), 1f),
                filter = (A("-vgacFilter") ?? "bilinear").ToLowerInvariant() == "point" ? "point" : "bilinear",
                mipmaps = (A("-vgacMipmaps") ?? "on").ToLowerInvariant() != "off",
                compress = (A("-vgacCompress") ?? "on").ToLowerInvariant() != "off",
                normalMap = (A("-vgacNormalMap") ?? "on").ToLowerInvariant() != "off",
                hair = (A("-vgacHair") ?? "off").ToLowerInvariant() == "on",
                hairShader = (A("-vgacHairShader") ?? "vgac").ToLowerInvariant() == "lit" ? "lit" : "vgac",
                hairCutout = (A("-vgacHairCutout") ?? "off").ToLowerInvariant() == "on",
                hairTwoSided = (A("-vgacHairTwoSided") ?? "off").ToLowerInvariant() == "on",
            };
            if (a.speedScale <= 0.01f) a.speedScale = 1f;
            return a;
        }
    }

    public static void Run()
    {
        var a = ImportArgs.Parse(Environment.GetCommandLineArgs(), null);
        RefreshClipFacing(a.name);  // Version 16: characters an older importer brought in, once
        var report = Import(a);
        if (!report.ok) EditorApplication.Exit(1);
    }

    // The import itself, shared by Run (batch) and the Editor hand-off. Never throws; writes <input>/unity_report.json
    // and returns the report.
    static Report Import(ImportArgs a)
    {
        var input = a.input;
        var name = a.name;
        var humanoid = a.humanoid;
        var category = a.category ?? "";
        float speedScale = a.speedScale;
        var report = new Report { request_id = a.requestId ?? "" };
        GameObject built = null;
        try
        {
            if (input == null || name == null) throw new Exception("missing -vgacInput or -vgacName");
            var assetDir = "Assets/VGAC/" + name;
            Directory.CreateDirectory(assetDir);
            var fbxSrc = Path.Combine(input, name + ".fbx");
            if (!File.Exists(fbxSrc)) fbxSrc = Directory.GetFiles(input, "*.fbx").FirstOrDefault() ?? fbxSrc;
            if (File.Exists(fbxSrc)) File.Copy(fbxSrc, Path.Combine(assetDir, name + ".fbx"), true);
            var msName = "T_" + name + "_MetallicSmoothness.png";
            foreach (var f in new[] { "base_color.png", "normal.png", "metallic.png", "roughness.png", "ao.png" })
            {
                if (f == "normal.png" && !a.normalMap)
                {
                    // A look without surface bumps: no normal map, and none left over from an earlier import.
                    var stale = assetDir + "/" + f;
                    if (File.Exists(stale)) AssetDatabase.DeleteAsset(stale);
                    continue;
                }
                var src = Path.Combine(input, f);
                if (File.Exists(src)) File.Copy(src, Path.Combine(assetDir, f), true);
            }
            var msSrc = Path.Combine(input, msName);
            if (!File.Exists(msSrc)) msSrc = Path.Combine(input, "metallic_smoothness.png");
            if (File.Exists(msSrc)) File.Copy(msSrc, Path.Combine(assetDir, msName), true);
            // Version 13: the hair's own textures; none left over from an earlier import when this one has no hair material
            foreach (var f in HairTextures(name))
            {
                var src = Path.Combine(input, f);
                var dst = assetDir + "/" + f;
                if (a.hair && File.Exists(src) && (f != "hair_normal.png" || a.normalMap)) File.Copy(src, dst, true);
                else if (File.Exists(dst)) AssetDatabase.DeleteAsset(dst);
            }
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            ConfigureTextures(assetDir, msName, report, a);

            // First import as Generic: the rest transforms it gives are what the explicit human mapping is built from.
            var fbxPath = assetDir + "/" + name + ".fbx";
            var importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
            if (importer == null) throw new Exception("FBX not imported: " + fbxPath);
            importer.materialImportMode = ModelImporterMaterialImportMode.None;
            importer.importNormals = ModelImporterNormals.Import;
            importer.importTangents = ModelImporterTangents.CalculateMikk;
            importer.animationType = ModelImporterAnimationType.Generic;
            importer.importAnimation = true;
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            importer.clipAnimations = new ModelImporterClipAnimation[0];
            // G17: lightmap UVs (UV1) for scenery and still props. Unity's defaults for the unwrap, with the margin
            // calculated from the lightmap resolution (40 texels per unit, objects at scale 1) so charts do not bleed.
            importer.generateSecondaryUV = LightmapCategories.Contains(category);
            if (importer.generateSecondaryUV)
            {
                importer.secondaryUVHardAngle = 88f;
                importer.secondaryUVAngleDistortion = 8f;
                importer.secondaryUVAreaDistortion = 15f;
                importer.secondaryUVMarginMethod = ModelImporterSecondaryUVMarginMethod.Calculate;
                importer.secondaryUVMinLightmapResolution = 40f;
                importer.secondaryUVMinObjectScale = 1f;
            }
            importer.SaveAndReimport();
            report.secondaryUV = importer.generateSecondaryUV;
            if (report.secondaryUV)
                report.secondaryUVSettings = "margin " + importer.secondaryUVMarginMethod + " (min lightmap resolution " + importer.secondaryUVMinLightmapResolution
                    + ", min object scale " + importer.secondaryUVMinObjectScale + "), hard angle " + importer.secondaryUVHardAngle
                    + ", angle distortion " + importer.secondaryUVAngleDistortion + ", area distortion " + importer.secondaryUVAreaDistortion;

            if (humanoid)
            {
                report.avatar_mapping = MakeHumanoid(importer, fbxPath, report, a.keepPose);
                humanoid = report.avatar_mapping != "generic";
            }
            else report.avatar_mapping = "none";

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
            if (model == null) throw new Exception("model load failed");

            var mat = BuildMaterial(assetDir, msName, report);
            AssetDatabase.CreateAsset(mat, assetDir + "/" + name + ".mat");
            Material hairMat = null;
            var staleHair = assetDir + "/" + name + "_Hair.mat";
            if (a.hair && File.Exists(assetDir + "/hair_base_color.png"))
            {
                hairMat = BuildHairMaterial(assetDir, name, report, a);
                AssetDatabase.CreateAsset(hairMat, staleHair);
                report.hairMaterial = staleHair;
            }
            else if (File.Exists(staleHair)) AssetDatabase.DeleteAsset(staleHair);

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
            built = instance;  // removed below even when a later step throws (an open Editor keeps its scene)
            instance.name = name;
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            void AssignMaterials(bool count)
            {
                foreach (var r in instance.GetComponentsInChildren<Renderer>(true))
                {
                    var sm = r is SkinnedMeshRenderer sk ? sk.sharedMesh : r.GetComponent<MeshFilter>()?.sharedMesh;
                    if (hairMat != null && sm != null && sm.subMeshCount >= 2 && !IsColliderName(r.name))
                    {
                        // the FBX's slot order: <name>_Body, then <name>_Hair
                        r.sharedMaterials = new[] { mat, hairMat };
                        if (!count) continue;
                        report.hairSubmeshes++;
                        if (r.name.EndsWith("_LOD0")) report.hairTriangles = (int)(sm.GetIndexCount(1) / 3);
                    }
                    else if (sm != null && sm.subMeshCount >= 2) r.sharedMaterials = Enumerable.Repeat(mat, sm.subMeshCount).ToArray();
                    else r.sharedMaterial = mat;
                }
            }
            AssignMaterials(true);
            if (hairMat != null && report.hairSubmeshes == 0) report.warnings.Add("hair material built but no mesh has a second sub-mesh");
            if (report.secondaryUV)
            {
                var r0 = renderers.FirstOrDefault(r => r.name.EndsWith("_LOD0")) ?? renderers.FirstOrDefault();
                var m0 = r0 is SkinnedMeshRenderer s0 ? s0.sharedMesh : r0?.GetComponent<MeshFilter>()?.sharedMesh;
                var uv1 = new List<Vector2>();
                if (m0 != null) m0.GetUVs(1, uv1);
                report.secondaryUVOnMesh = uv1.Count > 0;
            }

            // Hit boxes and the body shape for moving (G34): a rigged character gets trigger shapes on its bones and a
            // simple body shape on the root, which replaces the convex hull (a hull does not follow the animation).
            bool rigged = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true).Any(s => s.bones.Length > 0);
            // Unity takes a model's default pose from the first clip's first frame, not from the file's rest pose
            // (arms down for the hand-made library's idle). Hit boxes and sockets are fitted against the rest pose,
            // so the instance stands in its bind pose before anything is placed on its bones.
            if (rigged) report.bindPoseMoved = ResetToBindPose(instance);
            bool bodyShape = false;
            var hitPath = a.hitboxes;
            if (hitPath != null && File.Exists(hitPath) && rigged)
                bodyShape = AddHitboxes(instance, hitPath, a.movementShape ?? "auto", a.scale, report);
            // Sockets: child transforms of the bones from the FBX, checked and placed from sockets.json.
            if (rigged) PlaceSockets(instance, a.sockets, a.scale, report);
            // Spring bones (Version 15): set up in the bind pose, like the hit boxes and sockets.
            if (rigged && a.springs != null && File.Exists(a.springs)) AddSprings(instance, a.springs, a.scale, report);

            // Collision (G16): one component per collision mesh under a Colliders child of the root, of the kind
            // colliders.json names: box, sphere and capsule become primitive colliders fitted to the mesh's bounds; hull
            // and cylinder a convex MeshCollider; mesh (static scenery) a non-convex MeshCollider. The source meshes stay
            // in the model, switched off. A rigged character with a body shape for moving (G34) gets none of them.
            var colMeshes = instance.GetComponentsInChildren<MeshFilter>(true).Where(m => IsColliderName(m.name)).OrderBy(m => m.name).ToList();
            if (colMeshes.Count > 0)
            {
                if (!bodyShape) AddColliders(instance, colMeshes, ReadColliders(a.colliders, report), report);
                foreach (var mf in colMeshes) mf.gameObject.SetActive(false);
            }
            else if (!bodyShape) report.warnings.Add("no collider mesh");

            // LODGroup: Unity creates it from _LODn names; verify, else build one.
            var lodGroup = instance.GetComponentInChildren<LODGroup>();
            var lodRenderers = renderers.Where(r => r.name.Contains("_LOD") && !IsColliderName(r.name)).OrderBy(r => r.name).ToList();
            if (lodGroup == null && lodRenderers.Count > 0)
            {
                lodGroup = instance.AddComponent<LODGroup>();
                var lods = new LOD[lodRenderers.Count];
                float[] heights = { 0.6f, 0.3f, 0.12f, 0.03f };
                for (int i = 0; i < lodRenderers.Count; i++)
                    lods[i] = new LOD(i < heights.Length ? heights[i] : 0.01f, new[] { lodRenderers[i] });
                lodGroup.SetLODs(lods);
                lodGroup.RecalculateBounds();
                report.warnings.Add("LODGroup built by script, not by importer naming");
            }
            if (lodGroup != null)
            {
                report.lodCount = lodGroup.lodCount;
                foreach (var lod in lodGroup.GetLODs())
                {
                    int tris = 0;
                    foreach (var r in lod.renderers)
                    {
                        var mf = r.GetComponent<MeshFilter>(); var smr = r as SkinnedMeshRenderer;
                        var mesh = smr ? smr.sharedMesh : (mf ? mf.sharedMesh : null);
                        if (mesh) tris += mesh.triangles.Length / 3;
                    }
                    report.lodTriangles.Add(tris);
                }
            }
            else report.warnings.Add("no LODGroup");
            // G20 part one: foliage fades between detail levels instead of popping. Not time-animated: each level
            // fades over the last quarter of its screen-height range.
            if (lodGroup != null && CrossFadeCategories.Contains(category))
            {
                lodGroup.fadeMode = LODFadeMode.CrossFade;
                lodGroup.animateCrossFading = false;
                var fl = lodGroup.GetLODs();
                for (int i = 0; i < fl.Length; i++) fl[i].fadeTransitionWidth = CrossFadeWidth;
                lodGroup.SetLODs(fl);
                report.lodFadeWidth = CrossFadeWidth;
            }
            if (lodGroup != null) report.lodFadeMode = lodGroup.fadeMode.ToString();

            // G17: environment pieces are static for baked light and batching.
            if (category == "environment")
            {
                var flags = StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic;
                foreach (var t in instance.GetComponentsInChildren<Transform>(true)) GameObjectUtility.SetStaticEditorFlags(t.gameObject, flags);
                report.staticFlags.Add("ContributeGI");
                report.staticFlags.Add("BatchingStatic");
            }

            // Rig and avatar. In-place cycles with root motion off: the game moves the object, the clips animate it.
            var animator = instance.GetComponent<Animator>() ?? instance.AddComponent<Animator>();
            animator.applyRootMotion = false;
            var avatar = AssetDatabase.LoadAllAssetsAtPath(fbxPath).OfType<Avatar>().FirstOrDefault();
            if (avatar != null)
            {
                animator.avatar = avatar;
                report.avatar = avatar.name;
                report.humanoidValid = avatar.isHuman && avatar.isValid;
            }
            var smrs = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            if (smrs.Length > 0) report.bones = smrs[0].bones.Length;

            // Clips: the shared library for a valid Humanoid, else the clips baked into the model FBX itself.
            List<AnimationClip> clipSet = null;
            HashSet<string> addedClips = null;  // moves the person added ("Add a move"), by clip name
            var clipsSrc = a.clips;
            if (clipsSrc != null && File.Exists(clipsSrc) && avatar != null && avatar.isHuman && avatar.isValid)
            {
                var clipsPath = assetDir + "/clips.fbx";
                File.Copy(clipsSrc, clipsPath, true);
                AssetDatabase.ImportAsset(clipsPath, ImportAssetOptions.ForceSynchronousImport);
                var ci = AssetImporter.GetAtPath(clipsPath) as ModelImporter;
                ci.animationType = ModelImporterAnimationType.Human;
                ci.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                ci.importAnimation = true;
                ci.materialImportMode = ModelImporterMaterialImportMode.None;
                ci.clipAnimations = new ModelImporterClipAnimation[0];
                // Version 16: the library's author asks for Bake Axis Conversion; without it its rig's half turn is
                // baked into every looping move and the character plays them facing backwards.
                ci.bakeAxisConversion = true;
                ci.userData = FacingMark;
                ClearHumanDescription(ci);
                ci.SaveAndReimport();
                SetClipLoops(ci, report, null);
                // Moves the person added are only in the model's own FBX: add those to the shared set.
                var side = ReadClipInfo(a.clipInfo, report);
                addedClips = AddedClips(side);
                if (addedClips.Count > 0) SetClipLoops(importer, report, side, addedClips);
                FaceForward(fbxPath, new[] { ci, importer }, report);
                clipSet = LoadClips(clipsPath);
                if (addedClips.Count > 0) clipSet.AddRange(LoadClips(fbxPath).Where(c => addedClips.Contains(ShortName(c.name))));
            }
            else
            {
                if (clipsSrc != null) report.warnings.Add("clips library skipped: avatar not humanoid or file missing");
                var side = ReadClipInfo(a.clipInfo, report);
                SetClipLoops(importer, report, side);
                addedClips = AddedClips(side);
                if (avatar != null && avatar.isHuman && avatar.isValid) FaceForward(fbxPath, new[] { importer }, report);
                clipSet = LoadClips(fbxPath);
            }
            // Which measured moves the controller plays (by asset and name).
            foreach (var f in report.clipFacing)
            {
                var path = f.source == "shared" ? assetDir + "/clips.fbx" : fbxPath;
                f.inController = clipSet.Any(c => c.name == f.name && AssetDatabase.GetAssetPath(c) == path);
            }
            // Read Loop Time back from the imported clips: that is what the game gets.
            foreach (var info in report.clips)
            {
                var c = clipSet.FirstOrDefault(x => x.name == info.name);
                if (c != null) info.loop = AnimationUtility.GetAnimationClipSettings(c).loopTime;
            }
            if (clipSet.Count > 0)
            {
                var ctrl = BuildController(assetDir + "/" + name + ".controller", clipSet, humanoid, speedScale, report.controller, addedClips);
                animator.runtimeAnimatorController = ctrl;
                report.clipCount = clipSet.Count;
            }
            report.controller.applyRootMotion = animator.applyRootMotion;

            var prefabPath = assetDir + "/" + name + ".prefab";
            // An FBX with material slots (the hair's own material) is imported again by the clip settings above, which
            // puts the import's default materials back on the instance: the materials are assigned once more here.
            AssignMaterials(false);
            PrefabUtility.SaveAsPrefabAsset(instance, prefabPath);
            UnityEngine.Object.DestroyImmediate(instance);
            built = null;
            // What the game gets: the sockets as saved in the prefab.
            var saved = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            // What the game gets: LOD0's materials as saved in the prefab
            var savedR0 = saved != null ? saved.GetComponentsInChildren<Renderer>(true).FirstOrDefault(r => r.name.EndsWith("_LOD0")) : null;
            if (savedR0 != null) report.lod0Materials = savedR0.sharedMaterials.Select(m => m != null ? m.name : "none").ToList();
            if (hairMat != null && !report.lod0Materials.Contains(hairMat.name))
            {
                report.warnings.Add("the saved prefab does not carry the hair material");
                report.hairMaterial = "";
            }
            if (saved != null)
                foreach (var t in saved.GetComponentsInChildren<Transform>(true).Where(t => IsSocketName(t.name)).OrderBy(t => t.name))
                {
                    report.sockets.Add(t.name);
                    report.socketBones.Add(t.name + ">" + (t.parent != null ? t.parent.name : ""));
                    if (t.GetComponents<Component>().Length > 1) report.warnings.Add("socket " + t.name + " carries components");
                }
            report.prefab = prefabPath;
            // Items: a second prefab with them under their sockets; the bare prefab above stays clean.
            if (rigged && a.attachments != null) BuildEquipped(prefabPath, a.attachments, assetDir, name, input, report);
            report.ok = true;
            report.showcase = PlaceInShowcase(prefabPath, Arg("-vgacHdri"), report.warnings);
        }
        catch (Exception e)
        {
            report.ok = false;
            report.error = e.ToString();
        }
        finally
        {
            if (built != null) UnityEngine.Object.DestroyImmediate(built);
        }
        var json = JsonUtility.ToJson(report, true);
        try { File.WriteAllText(Path.Combine(input ?? ".", "unity_report.json"), json); }
        catch (Exception e) { Debug.LogWarning("VGAC: could not write unity_report.json: " + e.Message); }
        Debug.Log("VGAC_REPORT " + json);
        return report;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Editor hand-off (Version 6): requests the backend leaves in Assets/VGAC/<name>/_incoming/vgac_import.json.

    public const string RequestFile = "vgac_import.json";
    public const string DoneFile = "vgac_import.done.json";
    const string RequestRoot = "Assets/VGAC";

    [Serializable]
    class Request
    {
        public string request_id = "";
        public string importer_version = "";
        public string name = "";
        public List<string> args = new List<string>();
    }

    static bool sweeping;

    // Pending request files under Assets/VGAC, as project-relative paths. Cheap: no Assets/VGAC folder, no work.
    public static List<string> PendingRequests()
    {
        if (!Directory.Exists(RequestRoot)) return new List<string>();
        return Directory.GetFiles(RequestRoot, RequestFile, SearchOption.AllDirectories)
            .Select(p => p.Replace('\\', '/')).OrderBy(p => p, StringComparer.Ordinal).ToList();
    }

    // Run every pending request. Safe to call again: a finished request has been renamed, so it is not found twice.
    // Never throws. -> the number of requests handled.
    public static int Sweep()
    {
        if (sweeping) return 0;
        int done = 0;
        sweeping = true;
        try
        {
            foreach (var path in PendingRequests())
            {
                try { if (HandleRequest(path)) done++; }
                catch (Exception e) { Debug.LogError("VGAC: request " + path + " failed: " + e); }
            }
            if (done > 0) AssetDatabase.Refresh();
        }
        catch (Exception e) { Debug.LogError("VGAC: sweep failed: " + e); }
        finally { sweeping = false; }
        return done;
    }

    static bool HandleRequest(string path)
    {
        if (!File.Exists(path)) return false;  // handled already
        var dir = Path.GetDirectoryName(path).Replace('\\', '/');
        Request req;
        Report report;
        try { req = JsonUtility.FromJson<Request>(File.ReadAllText(path)); }
        catch (Exception e) { req = null; Debug.LogError("VGAC: " + path + " is not readable: " + e.Message); }
        if (req == null || req.args == null || req.args.Count == 0)
        {
            report = new Report { ok = false, error = "request file not readable: " + path, request_id = req?.request_id ?? "" };
            File.WriteAllText(Path.Combine(dir, "unity_report.json"), JsonUtility.ToJson(report, true));
        }
        else
        {
            var a = ImportArgs.Parse(req.args, dir);
            a.requestId = req.request_id ?? "";
            if (string.IsNullOrEmpty(a.name)) a.name = req.name;
            Debug.Log("VGAC: importing " + a.name + " (request " + a.requestId + ")");
            report = Import(a);  // writes <dir>/unity_report.json with the request id
            if (report.ok)
            {
                // The prefab has its own copies now; drop the incoming model and pictures so the project does not
                // carry a second, unconfigured copy of the model. The small JSON files stay as the record.
                foreach (var f in Directory.GetFiles(dir).Where(f => f.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase)
                                                                     || f.EndsWith(".png", StringComparison.OrdinalIgnoreCase)))
                {
                    var ap = f.Replace('\\', '/');
                    if (!AssetDatabase.DeleteAsset(ap)) { File.Delete(ap); if (File.Exists(ap + ".meta")) File.Delete(ap + ".meta"); }
                }
            }
        }
        MarkDone(path, dir + "/" + DoneFile);
        Debug.Log("VGAC: request " + (req?.request_id ?? "?") + (report.ok ? " imported: " + report.prefab : " failed: " + report.error));
        return true;
    }

    static void MarkDone(string from, string to)
    {
        if (File.Exists(to)) { if (!AssetDatabase.DeleteAsset(to)) { File.Delete(to); if (File.Exists(to + ".meta")) File.Delete(to + ".meta"); } }
        var err = AssetDatabase.MoveAsset(from, to);
        if (string.IsNullOrEmpty(err)) return;
        File.Move(from, to);  // the request was never imported as an asset (no refresh yet)
        if (File.Exists(from + ".meta")) File.Delete(from + ".meta");
    }

    // Batch check of the hand-off: the same sweep the Editor runs. Exit code 1 when any handled request failed.
    // Unity.exe -batchmode -quit -projectPath <project> -executeMethod VgacImport.ImportPending
    public static void ImportPending()
    {
        var pending = PendingRequests();
        RefreshClipFacing();
        int n = Sweep();
        bool failed = false;
        foreach (var p in pending)
        {
            var rp = Path.Combine(Path.GetDirectoryName(p), "unity_report.json");
            try { if (File.Exists(rp) && !JsonUtility.FromJson<Report>(File.ReadAllText(rp)).ok) failed = true; }
            catch (Exception) { failed = true; }
        }
        Debug.Log("VGAC_PENDING handled " + n + " of " + pending.Count);
        if (failed) EditorApplication.Exit(1);
    }

    [MenuItem("Tools/VGAC/Import pending assets")]
    static void ImportPendingMenu()
    {
        int n = Sweep();
        Debug.Log(n == 0 ? "VGAC: no pending assets." : "VGAC: imported " + n + " pending asset(s); see the Console for each result.");
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Collision shapes (G16, G35)

    static ColDoc ReadColliders(string path, Report report)
    {
        if (path == null || !File.Exists(path)) return null;
        try { return JsonUtility.FromJson<ColDoc>(File.ReadAllText(path)); }
        catch (Exception e) { report.warnings.Add("colliders.json not readable: " + e.Message); return null; }
    }

    static void AddColliders(GameObject root, List<MeshFilter> meshes, ColDoc doc, Report report)
    {
        var holder = new GameObject("Colliders");
        holder.transform.SetParent(root.transform, false);
        foreach (var mf in meshes)
        {
            var mesh = mf.sharedMesh;
            if (mesh == null) continue;
            var part = doc?.parts?.FirstOrDefault(p => p.name == mf.name);
            var kind = part != null && !string.IsNullOrEmpty(part.kind) ? part.kind : (mf.name.EndsWith("_collider_mesh") ? "mesh" : "hull");
            var go = new GameObject(mf.name);
            go.transform.SetParent(holder.transform, false);
            go.transform.SetPositionAndRotation(mf.transform.position, mf.transform.rotation);
            var ls = mf.transform.lossyScale; var hs = holder.transform.lossyScale;
            go.transform.localScale = new Vector3(ls.x / Mathf.Max(1e-6f, hs.x), ls.y / Mathf.Max(1e-6f, hs.y), ls.z / Mathf.Max(1e-6f, hs.z));
            var b = mesh.bounds;
            if (kind == "box")
            {
                var c = go.AddComponent<BoxCollider>(); c.center = b.center; c.size = b.size;
            }
            else if (kind == "sphere")
            {
                var c = go.AddComponent<SphereCollider>(); c.center = b.center; c.radius = Mathf.Max(b.extents.x, Mathf.Max(b.extents.y, b.extents.z));
            }
            else if (kind == "capsule")
            {
                var s = b.size;
                int dir = s.x >= s.y && s.x >= s.z ? 0 : (s.y >= s.z ? 1 : 2);
                var c = go.AddComponent<CapsuleCollider>();
                c.center = b.center; c.direction = dir; c.height = s[dir];
                c.radius = Mathf.Max(s[(dir + 1) % 3], s[(dir + 2) % 3]) / 2f;
            }
            else
            {
                var c = go.AddComponent<MeshCollider>();
                c.sharedMesh = mesh;
                c.convex = kind != "mesh";
                if (c.convex) report.colliderTrianglesMax = Math.Max(report.colliderTrianglesMax, mesh.triangles.Length / 3);
            }
            report.colliderVertices += mesh.vertexCount;
            report.colliderCount++;
            if (!report.colliderKinds.Contains(kind)) report.colliderKinds.Add(kind);
        }
        // Check: the shapes together should span the drawn model (metres, worst side of the two boxes).
        Physics.SyncTransforms();
        var cols = holder.GetComponentsInChildren<Collider>(true);
        var lod0 = root.GetComponentsInChildren<Renderer>(true).FirstOrDefault(r => r.name.EndsWith("_LOD0") || r.name == root.name);
        if (cols.Length > 0 && lod0 != null)
        {
            var cb = cols[0].bounds;
            foreach (var c in cols) cb.Encapsulate(c.bounds);
            var rb = lod0.bounds;
            report.colliderBoundsError = Mathf.Max((cb.min - rb.min).magnitude, (cb.max - rb.max).magnitude);
        }
    }

    static float ParseFloat(string s, float fallback) =>
        s != null && float.TryParse(s, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : fallback;

    // ---------------------------------------------------------------------------------------------------------------
    // Hit boxes (G34)

    // glTF model space to Unity model space for this FBX (Blender export, axis_forward -Z, axis_up Y): x flips.
    static Vector3 U(float[] v, float scale) => v == null || v.Length < 3 ? Vector3.zero : new Vector3(-v[0], v[1], v[2]) * scale;

    static bool AddHitboxes(GameObject root, string path, string choice, float scale, Report report)
    {
        var doc = JsonUtility.FromJson<HitDoc>(File.ReadAllText(path));
        if (doc == null) { report.warnings.Add("hit boxes: file not readable"); return false; }
        var all = root.GetComponentsInChildren<Transform>(true);
        Transform Find(string n) => string.IsNullOrEmpty(n) ? null
            : (all.FirstOrDefault(t => t.name == n) ?? all.FirstOrDefault(t => StripName(t.name) == StripName(n)));
        var rt = root.transform;
        float worst = 0f;
        foreach (var h in doc.hitboxes ?? new List<HitShape>())
        {
            var bone = Find(h.bone);
            if (bone == null)
            {
                if (!string.IsNullOrEmpty(h.bone)) report.warnings.Add("hit box " + h.name + ": bone " + h.bone + " not found; placed on the root");
                bone = rt;
            }
            var go = new GameObject(string.IsNullOrEmpty(h.name) ? "Hit" : h.name);
            go.transform.SetParent(bone, false);
            go.transform.localPosition = bone.InverseTransformPoint(rt.TransformPoint(U(h.center, scale)));
            var fwd = U(h.forward, 1f); var up = U(h.axis, 1f);
            if (up.sqrMagnitude < 1e-8f) up = Vector3.up;
            if (fwd.sqrMagnitude < 1e-8f || Vector3.Cross(fwd, up).sqrMagnitude < 1e-8f) fwd = Vector3.Cross(up, Vector3.right).sqrMagnitude > 1e-6f ? Vector3.Cross(Vector3.right, up) : Vector3.forward;
            go.transform.rotation = rt.rotation * Quaternion.LookRotation(fwd, up);
            // Undo any scale on the bone so collider sizes are in metres.
            var ls = bone.lossyScale;
            go.transform.localScale = new Vector3(1f / Mathf.Max(1e-6f, ls.x), 1f / Mathf.Max(1e-6f, ls.y), 1f / Mathf.Max(1e-6f, ls.z));
            if (h.kind == "sphere")
            {
                var c = go.AddComponent<SphereCollider>(); c.radius = h.radius * scale; c.isTrigger = true;
            }
            else if (h.kind == "box")
            {
                var c = go.AddComponent<BoxCollider>(); c.size = new Vector3(h.size[0], h.size[1], h.size[2]) * scale; c.isTrigger = true;
            }
            else
            {
                var c = go.AddComponent<CapsuleCollider>(); c.direction = 1; c.radius = h.radius * scale; c.height = h.height * scale; c.isTrigger = true;
            }
            // Check: the file's bone head should sit where the prefab's bone is.
            if (bone != rt && h.bone_head != null && h.bone_head.Length == 3)
                worst = Mathf.Max(worst, (rt.TransformPoint(U(h.bone_head, scale)) - bone.position).magnitude);
            report.hitboxCount++;
        }
        report.hitboxMaxBoneError = worst;

        var pick = string.IsNullOrEmpty(choice) || choice == "auto" ? (doc.movement != null ? doc.movement.@default : "none") : choice;
        HitShape m = doc.movement == null ? null : pick == "upright" ? doc.movement.upright : pick == "along" ? doc.movement.along : pick == "box" ? doc.movement.box : null;
        if (m == null)
        {
            if (pick != "none") report.warnings.Add("movement shape " + pick + " is not in the hit-box file");
            report.movementShape = "none";
            return false;
        }
        report.movementShape = pick;
        if (pick == "box")
        {
            var b = root.AddComponent<BoxCollider>();
            b.center = U(m.center, scale);
            b.size = new Vector3(Mathf.Abs(m.size[0]), Mathf.Abs(m.size[1]), Mathf.Abs(m.size[2])) * scale;
        }
        else
        {
            var c = root.AddComponent<CapsuleCollider>();
            c.center = U(m.center, scale);
            c.radius = m.radius * scale;
            c.height = m.height * scale;
            c.direction = Mathf.Clamp(m.direction, 0, 2);
        }
        return true;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Spring bones (Version 15)

    public static Type SpringType() =>
        AppDomain.CurrentDomain.GetAssemblies().Select(asm => asm.GetType("VgacSpringBones", false)).FirstOrDefault(t => t != null);

    static void AddSprings(GameObject root, string path, float scale, Report report)
    {
        var type = SpringType();
        if (type == null)
        {
            report.springWarning = "the spring script Assets/VgacScripts/VgacSpringBones.cs is not in the project; the chains sway only in the character's own clips";
            report.warnings.Add(report.springWarning);
            return;
        }
        try
        {
            var comp = root.GetComponent(type) ?? root.AddComponent(type);
            var summary = (string)type.GetMethod("Setup").Invoke(comp, new object[] { File.ReadAllText(path), scale });
            var chains = (Array)type.GetField("chains").GetValue(comp);
            report.springChains = chains.Length;
            foreach (var c in chains) report.springBones += ((Array)c.GetType().GetField("bones").GetValue(c)).Length;
            if (summary.Contains("not found")) { report.springWarning = summary; report.warnings.Add("spring bones: " + summary); }
        }
        catch (Exception e)
        {
            report.springWarning = "spring script setup failed: " + (e.InnerException ?? e).Message;
            report.warnings.Add(report.springWarning);
        }
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Sockets (Version 8)

    // Stand a rigged instance in its bind pose (the rest pose the mesh was skinned in). Returns how far the bone
    // that moved most was from it, in metres.
    static float ResetToBindPose(GameObject root)
    {
        var smr = root.GetComponentsInChildren<SkinnedMeshRenderer>(true)
            .Where(x => x.sharedMesh != null && x.bones.Length > 0 && x.sharedMesh.bindposes.Length == x.bones.Length)
            .OrderByDescending(x => x.bones.Length).FirstOrDefault();
        if (smr == null) return 0f;
        var binds = smr.sharedMesh.bindposes;
        var bones = smr.bones;
        var meshToWorld = smr.transform.localToWorldMatrix;
        int Depth(Transform t) { int d = 0; while (t.parent != null) { d++; t = t.parent; } return d; }
        float worst = 0f;
        // Parents first, so a child set afterwards keeps its place.
        foreach (var i in Enumerable.Range(0, bones.Length).Where(i => bones[i] != null).OrderBy(i => Depth(bones[i])))
        {
            var m = meshToWorld * binds[i].inverse;
            Vector3 pos = m.GetColumn(3);
            worst = Mathf.Max(worst, (bones[i].position - pos).magnitude);
            bones[i].SetPositionAndRotation(pos, m.rotation);
        }
        return worst;
    }

    static void PlaceSockets(GameObject root, string path, float scale, Report report)
    {
        var rt = root.transform;
        var all = rt.GetComponentsInChildren<Transform>(true);
        if (path == null || !File.Exists(path)) return;  // the FBX's own sockets stay as they are
        SocketDoc doc = null;
        try { doc = JsonUtility.FromJson<SocketDoc>(File.ReadAllText(path)); }
        catch (Exception e) { report.warnings.Add("sockets.json not readable: " + e.Message); return; }
        if (doc == null || doc.sockets == null) return;
        Transform Find(string n) => string.IsNullOrEmpty(n) ? null
            : (all.FirstOrDefault(t => t.name == n) ?? all.FirstOrDefault(t => StripName(t.name) == StripName(n)));
        float worst = 0f, worstAngle = 0f;
        foreach (var s in doc.sockets)
        {
            if (string.IsNullOrEmpty(s.name)) continue;
            var bone = Find(s.bone);
            if (bone == null)
            {
                report.warnings.Add("socket " + s.name + ": bone " + s.bone + " not found; placed on the root");
                bone = rt;
            }
            var want = rt.TransformPoint(U(s.position, scale));
            var fwd = rt.TransformDirection(U(s.forward, 1f));
            var up = rt.TransformDirection(U(s.up, 1f));
            if (fwd.sqrMagnitude < 1e-8f) fwd = rt.forward;
            if (up.sqrMagnitude < 1e-8f || Vector3.Cross(fwd, up).sqrMagnitude < 1e-8f) up = rt.up;
            var t = all.FirstOrDefault(x => x.name == s.name);
            if (t == null)
            {
                t = new GameObject(s.name).transform;
                report.socketsAdded++;
                report.warnings.Add("socket " + s.name + " was not in the FBX; added from sockets.json");
            }
            else
            {
                worst = Mathf.Max(worst, (t.position - want).magnitude);
                worstAngle = Mathf.Max(worstAngle, Mathf.Max(Vector3.Angle(t.forward, fwd), Vector3.Angle(t.up, up)));
                if (t.parent != bone) report.warnings.Add("socket " + s.name + " was under " + (t.parent ? t.parent.name : "nothing") + "; moved to " + bone.name);
            }
            t.SetParent(bone, true);
            t.position = want;
            t.rotation = Quaternion.LookRotation(fwd, up);
            // An item parented here keeps its own size: undo any scale on the bone.
            var ls = bone.lossyScale;
            t.localScale = new Vector3(1f / Mathf.Max(1e-6f, ls.x), 1f / Mathf.Max(1e-6f, ls.y), 1f / Mathf.Max(1e-6f, ls.z));
        }
        report.socketMaxError = worst;
        report.socketMaxAngle = worstAngle;
    }

    // Items: "<Name> (equipped).prefab", a variant of the bare prefab with each item's prefab (made by the item's own
    // import, Assets/VGAC/<item>/<item>.prefab) under its socket at the offset from attachments.json. The offset is in
    // the socket's glTF frame; Unity's import mirrors X, so the position flips x and the rotation flips y and z. An
    // item without its prefab falls back to its FBX from items/<item>/ (no material) with a warning. Held items' own
    // colliders become triggers, so they do not push the character's own body shape.
    static void BuildEquipped(string prefabPath, string path, string assetDir, string name, string input, Report report)
    {
        if (!File.Exists(path)) return;
        AttachDoc doc;
        try { doc = JsonUtility.FromJson<AttachDoc>(File.ReadAllText(path)); }
        catch (Exception e) { report.warnings.Add("attachments.json not readable: " + e.Message); return; }
        if (doc == null || doc.items == null || doc.items.Count == 0) return;
        var bare = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
        if (bare == null) return;
        var inst = (GameObject)PrefabUtility.InstantiatePrefab(bare);
        try
        {
            inst.name = name + " (equipped)";
            var all = inst.GetComponentsInChildren<Transform>(true);
            foreach (var it in doc.items)
            {
                var sock = all.FirstOrDefault(t => t.name == it.socket);
                if (sock == null) { report.warnings.Add("item " + it.item + ": no " + it.socket + " on the character"); continue; }
                GameObject src = string.IsNullOrEmpty(it.unity_prefab) ? null : AssetDatabase.LoadAssetAtPath<GameObject>(it.unity_prefab);
                if (src == null && it.files != null && !string.IsNullOrEmpty(it.files.fbx) && input != null)
                {
                    var fbx = Path.Combine(input, it.files.fbx);
                    if (File.Exists(fbx))
                    {
                        Directory.CreateDirectory(assetDir + "/items");
                        var dst = assetDir + "/items/" + Path.GetFileName(fbx);
                        File.Copy(fbx, dst, true);
                        AssetDatabase.ImportAsset(dst, ImportAssetOptions.ForceSynchronousImport);
                        src = AssetDatabase.LoadAssetAtPath<GameObject>(dst);
                        report.warnings.Add("item " + it.item + ": its prefab was not found, so its plain model is used");
                    }
                }
                if (src == null) { report.warnings.Add("item " + it.item + ": no prefab or model found"); continue; }
                var go = (GameObject)PrefabUtility.InstantiatePrefab(src);
                go.name = string.IsNullOrEmpty(it.item) ? src.name : it.item;
                go.transform.SetParent(sock, false);
                go.transform.localPosition = U(it.position, 1f);
                var q = it.rotation != null && it.rotation.Length == 4 ? it.rotation : new float[] { 0, 0, 0, 1 };
                go.transform.localRotation = new Quaternion(q[0], -q[1], -q[2], q[3]);
                go.transform.localScale = Vector3.one * (it.scale > 0 ? it.scale : 1f);
                var anim = go.GetComponent<Animator>();
                if (anim != null && anim.runtimeAnimatorController == null) UnityEngine.Object.DestroyImmediate(anim);
                foreach (var c in go.GetComponentsInChildren<Collider>(true))
                {
                    if (c is MeshCollider mc && !mc.convex) mc.enabled = false;
                    else c.isTrigger = true;
                }
                report.equippedItems.Add(go.name + ">" + it.socket);
            }
            if (report.equippedItems.Count == 0) return;
            var eqPath = assetDir + "/" + name + " (equipped).prefab";
            PrefabUtility.SaveAsPrefabAsset(inst, eqPath);
            report.equippedPrefab = eqPath;
        }
        finally
        {
            UnityEngine.Object.DestroyImmediate(inst);
        }
        // What the game gets: each item under its socket in the saved prefab.
        var saved = AssetDatabase.LoadAssetAtPath<GameObject>(report.equippedPrefab);
        var found = new List<string>();
        if (saved != null)
            foreach (var t in saved.GetComponentsInChildren<Transform>(true).Where(t => IsSocketName(t.name)))
                foreach (Transform child in t)
                    found.Add(child.name + ">" + t.name);
        foreach (var want in report.equippedItems.Where(w => !found.Contains(w)).ToList())
            report.warnings.Add("equipped prefab: " + want + " not found after saving");
        report.equippedItems = found;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Textures and material (G01, G02)

    static string[] HairTextures(string name) =>
        new[] { "hair_base_color.png", "hair_normal.png", "hair_ao.png", "hair_flow.png", "T_" + name + "_Hair_MetallicSmoothness.png" };

    // Version 13: the hair's own material. The VGAC/Hair shader on the built-in pipeline (unless -vgacHairShader lit);
    // the body's shader otherwise, with the hair's textures.
    static Material BuildHairMaterial(string assetDir, string name, Report report, ImportArgs a)
    {
        bool builtin = GraphicsSettings.currentRenderPipeline == null;
        // Version 17: cut-out or two-sided hair (from the library) takes VGAC/HairCutout; everything else VGAC/Hair.
        bool cutoutShader = a.hairCutout || a.hairTwoSided;
        var hairShader = Shader.Find(cutoutShader ? HairCutoutShaderName : HairShaderName);
        Material mat;
        var hs = "T_" + name + "_Hair_MetallicSmoothness.png";
        if (a.hairShader == "vgac" && builtin && hairShader != null)
        {
            mat = new Material(hairShader);
            var b = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/hair_base_color.png");
            var n = report.normalMap ? AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/hair_normal.png") : null;
            var o = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/hair_ao.png");
            var m = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/" + hs);
            var f = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/hair_flow.png");
            if (b) { mat.SetTexture("_MainTex", b); report.hairTexturesAssigned.Add("base_color"); }
            if (n) { mat.SetTexture("_BumpMap", n); report.hairTexturesAssigned.Add("normal"); }
            if (o) { mat.SetTexture("_OcclusionMap", o); report.hairTexturesAssigned.Add("ao"); }
            if (m) { mat.SetTexture("_MetallicGlossMap", m); report.hairTexturesAssigned.Add("metallic_smoothness"); }
            if (f) { mat.SetTexture("_FlowMap", f); report.hairTexturesAssigned.Add("flow"); }
            // the strand highlight in the hair's own colour family: a warm white for the primary band
            if (b) mat.SetColor("_SpecColor1", Color.Lerp(Color.white, AverageColour(b), 0.5f));
            if (cutoutShader)
            {
                mat.SetFloat("_Cutoff", a.hairCutout ? 0.5f : 0f);
                mat.SetFloat("_Cull", a.hairTwoSided ? 0f : 2f);
                mat.renderQueue = (int)RenderQueue.AlphaTest;
            }
        }
        else
        {
            if (a.hairShader == "vgac" && builtin && hairShader == null)
                report.warnings.Add((cutoutShader ? "VGAC/HairCutout shader not found (Assets/VgacShaders/VgacHairCutout.shader)"
                                                  : "VGAC/Hair shader not found (Assets/VgacShaders/VgacHair.shader)") + "; the hair uses the body's shader");
            var tmp = new Report { normalMap = report.normalMap };
            mat = BuildMaterialFrom(assetDir, "hair_base_color.png", "hair_normal.png", "hair_ao.png", hs, tmp);
            report.hairTexturesAssigned.AddRange(tmp.texturesAssigned);
            if (cutoutShader) SetLitCutout(mat, a);
        }
        report.hairCutout = a.hairCutout;
        report.hairTwoSided = a.hairTwoSided;
        mat.name = name + "_Hair";
        report.hairShader = mat.shader.name;
        return mat;
    }

    // Version 17: alpha clipping on a Standard, URP or HDRP Lit material for cut-out hair outside the built-in VGAC shader
    // (URP and HDRP not verified).
    static void SetLitCutout(Material mat, ImportArgs a)
    {
        if (a.hairCutout)
        {
            if (mat.HasProperty("_Cutoff")) mat.SetFloat("_Cutoff", 0.5f);
            if (mat.HasProperty("_AlphaCutoff")) mat.SetFloat("_AlphaCutoff", 0.5f);
            if (mat.HasProperty("_AlphaClip")) mat.SetFloat("_AlphaClip", 1f);
            if (mat.HasProperty("_AlphaCutoffEnable")) mat.SetFloat("_AlphaCutoffEnable", 1f);
            if (mat.HasProperty("_Mode")) mat.SetFloat("_Mode", 1f);  // Standard: Cutout
            mat.EnableKeyword("_ALPHATEST_ON");
            mat.SetOverrideTag("RenderType", "TransparentCutout");
            mat.renderQueue = (int)RenderQueue.AlphaTest;
        }
        if (a.hairTwoSided)
        {
            if (mat.HasProperty("_Cull")) mat.SetFloat("_Cull", 0f);
            if (mat.HasProperty("_CullMode")) mat.SetFloat("_CullMode", 0f);
            if (mat.HasProperty("_DoubleSidedEnable")) mat.SetFloat("_DoubleSidedEnable", 1f);
        }
    }

    static Color AverageColour(Texture2D t)
    {
        try
        {
            var path = AssetDatabase.GetAssetPath(t);
            var bytes = File.ReadAllBytes(path);
            var tex = new Texture2D(2, 2);
            tex.LoadImage(bytes);
            var px = tex.GetPixels32();
            double r = 0, g = 0, bl = 0; int n = 0;
            for (int i = 0; i < px.Length; i += 17)
            {
                if (px[i].r + px[i].g + px[i].b < 12) continue;  // the empty space between the pieces
                r += px[i].r; g += px[i].g; bl += px[i].b; n++;
            }
            UnityEngine.Object.DestroyImmediate(tex);
            return n == 0 ? Color.white : new Color((float)(r / n / 255.0), (float)(g / n / 255.0), (float)(bl / n / 255.0));
        }
        catch { return Color.white; }
    }

    static void ConfigureTextures(string assetDir, string msName, Report report, ImportArgs a)
    {
        var point = a != null && a.filter == "point";
        var mips = a == null || a.mipmaps;
        var compress = a == null || a.compress;
        report.normalMap = a == null || a.normalMap;
        report.textureFilter = point ? "Point" : "Bilinear";
        report.textureMipmaps = mips;
        report.textureCompression = compress ? "Compressed" : "Uncompressed";
        var names = new List<string> { "base_color.png", "normal.png", "metallic.png", "roughness.png", "ao.png", msName };
        var hairMs = "T_" + msName.Substring(2, msName.Length - 2 - "_MetallicSmoothness.png".Length) + "_Hair_MetallicSmoothness.png";
        names.AddRange(new[] { "hair_base_color.png", "hair_normal.png", "hair_ao.png", "hair_flow.png", hairMs });
        foreach (var t in names)
        {
            var ti = AssetImporter.GetAtPath(assetDir + "/" + t) as TextureImporter;
            if (ti == null) continue;
            if (t == "base_color.png" || t == "hair_base_color.png")
            {
                ti.textureType = TextureImporterType.Default;
                ti.sRGBTexture = true;
                if (t == "hair_base_color.png" && a != null && a.hairCutout)
                {
                    // Version 17: cut-out hair keeps its alpha, and the mipmaps keep the cards' coverage at the cutoff
                    ti.alphaSource = TextureImporterAlphaSource.FromInput;
                    ti.alphaIsTransparency = true;
                    ti.mipMapsPreserveCoverage = true;
                    ti.alphaTestReferenceValue = 0.5f;
                }
            }
            else if (t == "normal.png" || t == "hair_normal.png")
            {
                ti.textureType = TextureImporterType.NormalMap;
                ti.sRGBTexture = false;
            }
            else
            {
                // Data maps: linear, alpha kept as authored (smoothness lives in the alpha of the metal-and-shine map).
                ti.textureType = TextureImporterType.Default;
                ti.sRGBTexture = false;
                ti.alphaSource = TextureImporterAlphaSource.FromInput;
                ti.alphaIsTransparency = false;
            }
            // Recipes: crisp looks sample whole texels (Point) with no mipmaps or compression, so a 256 px pixel-art
            // texture stays exact; other looks keep Unity's defaults (Bilinear, mipmaps, compressed).
            ti.filterMode = point ? FilterMode.Point : FilterMode.Bilinear;
            ti.mipmapEnabled = mips;
            ti.textureCompression = compress ? TextureImporterCompression.Compressed : TextureImporterCompression.Uncompressed;
            if (point) ti.npotScale = TextureImporterNPOTScale.None;
            // Unity imports at most 2048 by default: a 4096 base colour (High face detail on a 4096 platform) would
            // silently arrive at half its size. The import keeps the file's own size, never more.
            int sw, sh;
            ti.GetSourceTextureWidthAndHeight(out sw, out sh);
            var need = Mathf.Min(16384, Mathf.NextPowerOfTwo(Mathf.Max(sw, sh)));
            if (need > ti.maxTextureSize) ti.maxTextureSize = need;
            ti.SaveAndReimport();
            report.textureSizes.Add(t + ":" + Mathf.Max(sw, sh) + ":" + ti.maxTextureSize);
            report.textureSettings.Add(t + ": " + ti.textureType + (ti.sRGBTexture ? ", sRGB" : ", linear") + (t == msName || t == hairMs ? ", alpha " + ti.alphaSource : "")
                + ", " + ti.filterMode + (ti.mipmapEnabled ? ", mipmaps" : ", no mipmaps") + ", " + ti.textureCompression);
        }
    }

    static Shader PickShader()
    {
        // URP Lit when a URP pipeline is active, HDRP Lit for HDRP, else the built-in Standard shader. A project that
        // merely has the URP package but no active pipeline renders with built-in, so Standard is right there.
        var rp = GraphicsSettings.currentRenderPipeline;
        if (rp != null)
        {
            var typeName = rp.GetType().Name;
            if (typeName.Contains("HD")) return Shader.Find("HDRP/Lit") ?? Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
            return Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
        }
        return Shader.Find("Standard");
    }

    static Material BuildMaterial(string assetDir, string msName, Report report) =>
        BuildMaterialFrom(assetDir, "base_color.png", "normal.png", "ao.png", msName, report);

    static Material BuildMaterialFrom(string assetDir, string baseName, string normalName, string aoName, string msName, Report report)
    {
        var shader = PickShader();
        var mat = new Material(shader);
        report.shader = shader.name;
        bool urp = shader.name.StartsWith("Universal Render Pipeline");
        var baseTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/" + baseName);
        var normalTex = report.normalMap ? AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/" + normalName) : null;
        var aoTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/" + aoName);
        var msTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/" + msName);
        if (baseTex)
        {
            if (mat.HasProperty("_BaseMap")) mat.SetTexture("_BaseMap", baseTex);
            if (mat.HasProperty("_MainTex")) mat.SetTexture("_MainTex", baseTex);
            report.texturesAssigned.Add("base_color");
        }
        if (normalTex)
        {
            mat.SetTexture("_BumpMap", normalTex);
            mat.EnableKeyword("_NORMALMAP");
            report.texturesAssigned.Add("normal");
        }
        if (aoTex)
        {
            mat.SetTexture("_OcclusionMap", aoTex);
            if (mat.HasProperty("_OcclusionStrength")) mat.SetFloat("_OcclusionStrength", 1f);
            mat.EnableKeyword("_OCCLUSIONMAP");
            report.texturesAssigned.Add("ao");
        }
        if (msTex && mat.HasProperty("_MetallicGlossMap"))
        {
            // Metallic in R, smoothness (1 - roughness) in A. URP Lit: _METALLICSPECGLOSSMAP, smoothness from the
            // metallic map's alpha (channel 0), _Smoothness scales the alpha. Standard: _METALLICGLOSSMAP, _GlossMapScale.
            mat.SetTexture("_MetallicGlossMap", msTex);
            if (mat.HasProperty("_Metallic")) mat.SetFloat("_Metallic", 1f);
            if (mat.HasProperty("_SmoothnessTextureChannel")) mat.SetFloat("_SmoothnessTextureChannel", 0f);
            mat.DisableKeyword("_SMOOTHNESS_TEXTURE_ALBEDO_CHANNEL_A");
            if (urp)
            {
                mat.SetFloat("_Smoothness", 1f);
                mat.EnableKeyword("_METALLICSPECGLOSSMAP");
            }
            else
            {
                if (mat.HasProperty("_GlossMapScale")) mat.SetFloat("_GlossMapScale", 1f);
                if (mat.HasProperty("_Glossiness")) mat.SetFloat("_Glossiness", 1f);
                if (mat.HasProperty("_Smoothness")) mat.SetFloat("_Smoothness", 1f);
                mat.EnableKeyword("_METALLICGLOSSMAP");
            }
            report.texturesAssigned.Add("metallic_smoothness");
        }
        return mat;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Humanoid (G26): an explicit HumanDescription from the bone names, or from the skeleton's shape when the names
    // do not describe a person; then Unity's auto-mapping; then Generic.

    static readonly Dictionary<string, HumanBodyBones> NameMap = new Dictionary<string, HumanBodyBones>
    {
        { "Hips", HumanBodyBones.Hips }, { "Spine", HumanBodyBones.Spine }, { "Spine1", HumanBodyBones.Chest },
        { "Spine2", HumanBodyBones.UpperChest }, { "Neck", HumanBodyBones.Neck }, { "Head", HumanBodyBones.Head },
        { "LeftShoulder", HumanBodyBones.LeftShoulder }, { "RightShoulder", HumanBodyBones.RightShoulder },
        { "LeftArm", HumanBodyBones.LeftUpperArm }, { "RightArm", HumanBodyBones.RightUpperArm },
        { "LeftForeArm", HumanBodyBones.LeftLowerArm }, { "RightForeArm", HumanBodyBones.RightLowerArm },
        { "LeftHand", HumanBodyBones.LeftHand }, { "RightHand", HumanBodyBones.RightHand },
        { "LeftUpLeg", HumanBodyBones.LeftUpperLeg }, { "RightUpLeg", HumanBodyBones.RightUpperLeg },
        { "LeftLeg", HumanBodyBones.LeftLowerLeg }, { "RightLeg", HumanBodyBones.RightLowerLeg },
        { "LeftFoot", HumanBodyBones.LeftFoot }, { "RightFoot", HumanBodyBones.RightFoot },
        { "LeftToeBase", HumanBodyBones.LeftToes }, { "RightToeBase", HumanBodyBones.RightToes },
    };

    static readonly string[] FingerNames = { "Thumb", "Index", "Middle", "Ring", "Pinky" };
    static readonly string[] FingerEnum = { "Thumb", "Index", "Middle", "Ring", "Little" };
    static readonly string[] Phalanx = { "Proximal", "Intermediate", "Distal" };

    static HumanBodyBones FingerBone(bool left, int finger, int phalanx) =>
        (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), (left ? "Left" : "Right") + FingerEnum[finger] + Phalanx[phalanx]);

    static string StripName(string n)
    {
        var s = n;
        int bar = s.LastIndexOf('|');
        if (bar >= 0) s = s.Substring(bar + 1);
        if (s.StartsWith("mixamorig", StringComparison.OrdinalIgnoreCase))
        {
            s = s.Substring(9);
            if (s.StartsWith(":") || s.StartsWith("_")) s = s.Substring(1);
        }
        int colon = s.LastIndexOf(':');
        if (colon >= 0) s = s.Substring(colon + 1);
        return s;
    }

    static bool IsAncestor(Transform a, Transform b)
    {
        for (var p = b.parent; p != null; p = p.parent) if (p == a) return true;
        return false;
    }

    class Frame { public Vector3 up = Vector3.up, fwd = Vector3.forward, right = Vector3.right; public Vector3 origin; }

    static List<Transform> BoneChildren(Transform t, HashSet<Transform> bones) =>
        t.Cast<Transform>().Where(c => bones.Contains(c)).ToList();

    static IEnumerable<Transform> Subtree(Transform t, HashSet<Transform> bones)
    {
        yield return t;
        foreach (var c in BoneChildren(t, bones)) foreach (var d in Subtree(c, bones)) yield return d;
    }

    // Follow a single-child path; include the first bone with zero or several children and stop there.
    static List<Transform> Chain(Transform start, HashSet<Transform> bones)
    {
        var chain = new List<Transform> { start };
        var cur = start;
        while (true)
        {
            var kids = BoneChildren(cur, bones);
            if (kids.Count != 1) break;
            cur = kids[0];
            chain.Add(cur);
        }
        return chain;
    }

    // Follow the child with the largest subtree to the end (legs: up leg, leg, foot, toes).
    static List<Transform> LongChain(Transform start, HashSet<Transform> bones)
    {
        var chain = new List<Transform> { start };
        var cur = start;
        while (true)
        {
            var kids = BoneChildren(cur, bones);
            if (kids.Count == 0) break;
            cur = kids.OrderByDescending(k => Subtree(k, bones).Count()).First();
            chain.Add(cur);
        }
        return chain;
    }

    static Frame MakeFrame(Transform hips, List<List<Transform>> legs)
    {
        var f = new Frame { origin = hips.position };
        var fwd = Vector3.zero;
        foreach (var leg in legs)
            if (leg.Count >= 4) fwd += Vector3.ProjectOnPlane(leg[3].position - leg[2].position, Vector3.up);
        if (fwd.sqrMagnitude < 1e-8f) fwd = Vector3.ProjectOnPlane(hips.root.forward, Vector3.up);
        f.fwd = fwd.normalized;
        f.up = Vector3.up;
        f.right = Vector3.Cross(f.up, f.fwd).normalized;  // Unity is left-handed: Cross(up, forward) = right
        return f;
    }

    static float Lateral(Frame f, Vector3 p) => Vector3.Dot(p - f.origin, f.right);

    static void AddFingers(Dictionary<HumanBodyBones, Transform> map, Transform hand, bool left, HashSet<Transform> bones)
    {
        // Finger kind and joint number come from the names (LeftHandIndex1 and so on); the side comes from the hand
        // the finger hangs from, because some generated rigs carry the wrong side in the name.
        foreach (var child in BoneChildren(hand, bones))
        {
            var n = StripName(child.name);
            int finger = -1;
            for (int i = 0; i < FingerNames.Length; i++) if (n.Contains("Hand" + FingerNames[i])) finger = i;
            if (finger < 0) continue;
            var chain = Chain(child, bones);
            for (int j = 0; j < Math.Min(3, chain.Count); j++)
            {
                var fb = FingerBone(left, finger, j);
                if (!map.ContainsKey(fb)) map[fb] = chain[j];
            }
        }
    }

    static Dictionary<HumanBodyBones, Transform> MapByNames(Transform root, HashSet<Transform> bones)
    {
        var map = new Dictionary<HumanBodyBones, Transform>();
        foreach (var t in bones)
        {
            var n = StripName(t.name);
            if (n.StartsWith("Extra_", StringComparison.OrdinalIgnoreCase)) continue;
            if (NameMap.TryGetValue(n, out var hb)) { if (!map.ContainsKey(hb)) map[hb] = t; continue; }
            for (int side = 0; side < 2; side++)
            {
                var prefix = (side == 0 ? "Left" : "Right") + "Hand";
                if (!n.StartsWith(prefix)) continue;
                var rest = n.Substring(prefix.Length);
                for (int i = 0; i < FingerNames.Length; i++)
                {
                    if (!rest.StartsWith(FingerNames[i])) continue;
                    if (int.TryParse(rest.Substring(FingerNames[i].Length), out var num) && num >= 1 && num <= 3)
                    {
                        var fb = FingerBone(side == 0, i, num - 1);
                        if (!map.ContainsKey(fb)) map[fb] = t;
                    }
                }
            }
        }
        return map;
    }

    // Does the mapping describe a person standing in this frame: hierarchy right, head on the centre line above the
    // shoulders, left limbs on the left and right limbs on the right?
    static string CheckMapping(Dictionary<HumanBodyBones, Transform> map, Frame f)
    {
        for (int i = 0; i < HumanTrait.BoneCount; i++)
            if (HumanTrait.RequiredBone(i) && !map.ContainsKey((HumanBodyBones)i)) return "missing " + HumanTrait.BoneName[i];
        foreach (var kv in map)
        {
            int p = HumanTrait.GetParentBone((int)kv.Key);
            while (p >= 0 && !map.ContainsKey((HumanBodyBones)p)) p = HumanTrait.GetParentBone(p);
            if (p >= 0 && !IsAncestor(map[(HumanBodyBones)p], kv.Value)) return "hierarchy: " + kv.Key + " is not under " + (HumanBodyBones)p;
        }
        var head = map[HumanBodyBones.Head].position;
        var la = map[HumanBodyBones.LeftUpperArm].position; var ra = map[HumanBodyBones.RightUpperArm].position;
        float width = Mathf.Abs(Lateral(f, ra) - Lateral(f, la));
        if (Mathf.Abs(Lateral(f, head)) > 0.25f * width) return "head is off the centre line";
        if (head.y <= la.y || head.y <= ra.y) return "head is below the shoulders";
        // Upright: the head stands well above the hips (at least half the leg length) and the arms start above the
        // hips. A four-legged skeleton has a similar tree but fails this.
        var hipY = map[HumanBodyBones.Hips].position.y;
        float leg = hipY - 0.5f * (map[HumanBodyBones.LeftFoot].position.y + map[HumanBodyBones.RightFoot].position.y);
        if (leg <= 0f || head.y - hipY < 0.5f * leg) return "not upright (head not well above the hips)";
        if (la.y <= hipY || ra.y <= hipY) return "not upright (arms start below the hips)";
        foreach (var pair in new[] { (HumanBodyBones.LeftUpperArm, -1), (HumanBodyBones.LeftHand, -1), (HumanBodyBones.LeftUpperLeg, -1), (HumanBodyBones.LeftFoot, -1),
                                     (HumanBodyBones.RightUpperArm, 1), (HumanBodyBones.RightHand, 1), (HumanBodyBones.RightUpperLeg, 1), (HumanBodyBones.RightFoot, 1) })
            if (Mathf.Sign(Lateral(f, map[pair.Item1].position)) != pair.Item2) return pair.Item1 + " is on the wrong side";
        return null;
    }

    // Read the person from the skeleton's shape: legs go down from the hips, the spine goes up to a branch with the
    // neck in the middle and an arm on each side.
    static Dictionary<HumanBodyBones, Transform> MapByShape(Transform hips, HashSet<Transform> bones, Frame f, out string why)
    {
        why = null;
        var map = new Dictionary<HumanBodyBones, Transform>();
        var kids = BoneChildren(hips, bones);
        if (kids.Count < 3) { why = "hips have fewer than three children"; return null; }
        var spineRoot = kids.OrderByDescending(k => Subtree(k, bones).Max(t => t.position.y)).First();
        var legRoots = kids.Where(k => k != spineRoot).OrderBy(k => Subtree(k, bones).Min(t => t.position.y)).Take(2).ToList();
        foreach (var lr in legRoots)
        {
            var leg = LongChain(lr, bones);
            if (leg.Count < 3) { why = "a leg has fewer than three bones"; return null; }
            bool left = Lateral(f, lr.position) < 0;
            map[left ? HumanBodyBones.LeftUpperLeg : HumanBodyBones.RightUpperLeg] = leg[0];
            map[left ? HumanBodyBones.LeftLowerLeg : HumanBodyBones.RightLowerLeg] = leg[1];
            map[left ? HumanBodyBones.LeftFoot : HumanBodyBones.RightFoot] = leg[2];
            if (leg.Count >= 4) map[left ? HumanBodyBones.LeftToes : HumanBodyBones.RightToes] = leg[3];
        }
        map[HumanBodyBones.Hips] = hips;
        var spine = Chain(spineRoot, bones);
        var branch = spine[spine.Count - 1];
        var top = BoneChildren(branch, bones);
        if (top.Count < 3) { why = "no branch with a neck and two arms"; return null; }
        map[HumanBodyBones.Spine] = spine[0];
        if (spine.Count >= 2) map[HumanBodyBones.Chest] = spine[1];
        if (spine.Count >= 3) map[HumanBodyBones.UpperChest] = spine[spine.Count - 1];
        var byLat = top.Select(c => (c, lat: Subtree(c, bones).Average(t => Lateral(f, t.position)))).OrderBy(x => x.lat).ToList();
        var leftRoot = byLat.First().c; var rightRoot = byLat.Last().c;
        var neckRoot = top.Where(c => c != leftRoot && c != rightRoot).OrderBy(c => Mathf.Abs(byLat.First(x => x.c == c).lat)).First();
        var neck = Chain(neckRoot, bones);
        if (neck.Count >= 2) { map[HumanBodyBones.Neck] = neck[0]; map[HumanBodyBones.Head] = neck[1]; }
        else map[HumanBodyBones.Head] = neck[0];
        foreach (var (armRoot, left) in new[] { (leftRoot, true), (rightRoot, false) })
        {
            var arm = Chain(armRoot, bones);
            if (arm.Count < 3) { why = "an arm has fewer than three bones"; return null; }
            int o = arm.Count >= 4 ? 1 : 0;
            if (o == 1) map[left ? HumanBodyBones.LeftShoulder : HumanBodyBones.RightShoulder] = arm[0];
            map[left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm] = arm[o];
            map[left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm] = arm[o + 1];
            map[left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand] = arm[o + 2];
            AddFingers(map, arm[o + 2], left, bones);
        }
        return map;
    }

    static void Align(Transform bone, Vector3 target, Vector3 dir)
    {
        var from = target - bone.position;
        if (from.sqrMagnitude < 1e-10f) return;
        bone.rotation = Quaternion.FromToRotation(from, dir) * bone.rotation;
    }

    // SkinToken rigs rest close to an A-pose (arms well below horizontal). Unity's humanoid retargeting treats the
    // skeleton pose in the HumanDescription as the T-pose, so arms are turned out level and legs straight down.
    static void EnforceTPose(Dictionary<HumanBodyBones, Transform> map, Frame f)
    {
        Transform T(HumanBodyBones b) => map.TryGetValue(b, out var t) ? t : null;
        foreach (var left in new[] { true, false })
        {
            var dir = left ? -f.right : f.right;
            var ua = T(left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm);
            var la = T(left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm);
            var hand = T(left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand);
            var mid = T(left ? HumanBodyBones.LeftMiddleProximal : HumanBodyBones.RightMiddleProximal);
            if (ua && la) Align(ua, la.position, dir);
            if (la && hand) Align(la, hand.position, dir);
            if (hand && mid) Align(hand, mid.position, dir);
            var ul = T(left ? HumanBodyBones.LeftUpperLeg : HumanBodyBones.RightUpperLeg);
            var ll = T(left ? HumanBodyBones.LeftLowerLeg : HumanBodyBones.RightLowerLeg);
            var foot = T(left ? HumanBodyBones.LeftFoot : HumanBodyBones.RightFoot);
            if (ul && ll) Align(ul, ll.position, -f.up);
            if (ll && foot) Align(ll, foot.position, -f.up);
        }
    }

    static bool AvatarOk(string fbxPath)
    {
        var a = AssetDatabase.LoadAllAssetsAtPath(fbxPath).OfType<Avatar>().FirstOrDefault();
        return a != null && a.isHuman && a.isValid;
    }

    static string MakeHumanoid(ModelImporter importer, string fbxPath, Report report, bool keepPose)
    {
        var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
        GameObject clone = null;
        try
        {
            if (model != null)
            {
                clone = UnityEngine.Object.Instantiate(model);
                clone.name = model.name;
                var smr = clone.GetComponentsInChildren<SkinnedMeshRenderer>(true).OrderByDescending(s => s.bones.Length).FirstOrDefault();
                var bones = new HashSet<Transform>(smr != null ? smr.bones.Where(b => b != null) : new Transform[0]);
                // Bones the skin does not reference (end bones) are still part of the skeleton.
                foreach (var b in bones.ToList()) foreach (var d in b.GetComponentsInChildren<Transform>(true)) if (d.GetComponent<Renderer>() == null) bones.Add(d);
                // Attachment sockets hang under bones but are not bones: they must not change the shape-based mapping.
                bones.RemoveWhere(t => IsSocketName(t.name));
                // Spring chains (Version 15) hang from the head, chest or hips: never a neck, an arm or a leg.
                bones.RemoveWhere(t => IsSpringName(t.name));
                var hips = bones.Where(b => b.parent == null || !bones.Contains(b.parent)).OrderByDescending(b => Subtree(b, bones).Count()).FirstOrDefault();
                if (hips != null)
                {
                    var legs = BoneChildren(hips, bones).Select(k => LongChain(k, bones)).Where(c => c.Count >= 3)
                        .OrderBy(c => c.Min(t => t.position.y)).Take(2).ToList();
                    var frame = MakeFrame(hips, legs);
                    var map = MapByNames(clone.transform, bones);
                    var problem = CheckMapping(map, frame);
                    string by = "names";
                    if (problem != null)
                    {
                        report.warnings.Add("bone names do not describe a person (" + problem + "); mapped from the skeleton's shape");
                        map = MapByShape(hips, bones, frame, out var why);
                        by = "shape";
                        problem = map == null ? why : CheckMapping(map, frame);
                    }
                    if (problem == null)
                    {
                        if (!keepPose) { EnforceTPose(map, frame); report.tPoseApplied = true; }
                        var desc = importer.humanDescription;
                        desc.human = map.Select(kv => new HumanBone
                        {
                            humanName = HumanTrait.BoneName[(int)kv.Key],
                            boneName = kv.Value.name,
                            limit = new HumanLimit { useDefaultValues = true },
                        }).ToArray();
                        desc.skeleton = clone.GetComponentsInChildren<Transform>(true).Select(t => new SkeletonBone
                        {
                            name = t.name,
                            position = t.localPosition,
                            rotation = t.localRotation,
                            scale = t.localScale,
                        }).ToArray();
                        if (desc.upperArmTwist <= 0f) { desc.upperArmTwist = 0.5f; desc.lowerArmTwist = 0.5f; desc.upperLegTwist = 0.5f; desc.lowerLegTwist = 0.5f; }
                        if (desc.armStretch <= 0f) { desc.armStretch = 0.05f; desc.legStretch = 0.05f; }
                        desc.hasTranslationDoF = false;
                        importer.animationType = ModelImporterAnimationType.Human;
                        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                        importer.humanDescription = desc;
                        importer.SaveAndReimport();
                        if (AvatarOk(fbxPath))
                        {
                            report.avatar_mapping_by = by;
                            report.humanBonesMapped = map.Count;
                            return "explicit";
                        }
                        report.warnings.Add("explicit human mapping (" + by + ") was not accepted by Unity");
                        report.tPoseApplied = false;
                    }
                    else report.warnings.Add("explicit human mapping failed: " + problem);
                }
                else report.warnings.Add("explicit human mapping failed: no skinned skeleton");
            }
        }
        finally
        {
            if (clone != null) UnityEngine.Object.DestroyImmediate(clone);
        }

        // Unity's own auto-mapping.
        var empty = importer.humanDescription;
        empty.human = new HumanBone[0];
        empty.skeleton = new SkeletonBone[0];
        importer.humanDescription = empty;
        importer.animationType = ModelImporterAnimationType.Human;
        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        importer.SaveAndReimport();
        if (AvatarOk(fbxPath)) return "auto";

        report.warnings.Add("humanoid mapping failed; imported as Generic");
        importer.animationType = ModelImporterAnimationType.Generic;
        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        importer.SaveAndReimport();
        return "generic";
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Clips (G32) and the controller (G33)

    static readonly HashSet<string> CyclicWords = new HashSet<string>
    {
        "idle", "walk", "run", "trot", "jog", "sprint", "swim", "slither", "crawl", "fly", "flap", "glide", "hover",
        "gallop", "canter", "strafe", "breathe", "breathing",
    };
    static readonly HashSet<string> OneShotWords = new HashSet<string> { "attack", "hit", "death", "die", "dying", "dead" };
    static readonly HashSet<string> NeutralWords = new HashSet<string> { "loop", "fwd", "forward", "inplace" };

    static string ShortName(string clip)
    {
        int bar = clip.LastIndexOf('|');
        return bar >= 0 ? clip.Substring(bar + 1) : clip;
    }

    static List<string> Words(string clip)
    {
        var s = ShortName(clip);
        var words = new List<string>();
        var cur = new System.Text.StringBuilder();
        for (int i = 0; i < s.Length; i++)
        {
            char ch = s[i];
            bool boundary = !char.IsLetter(ch) || (char.IsUpper(ch) && i > 0 && char.IsLower(s[i - 1]));
            if (boundary && cur.Length > 0) { words.Add(cur.ToString().ToLowerInvariant()); cur.Clear(); }
            if (char.IsLetter(ch)) cur.Append(ch);
        }
        if (cur.Length > 0) words.Add(cur.ToString().ToLowerInvariant());
        return words;
    }

    static bool IsCyclic(string clip)
    {
        var w = Words(clip);
        if (w.Any(OneShotWords.Contains)) return false;
        return w.Any(CyclicWords.Contains) || clip.IndexOf("loop", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    // anim_clips.json from the rig stage (procanim.py): the loop flag per clip, which the GLB and FBX do not carry.
    [Serializable] class SidecarClip { public string name = ""; public bool loop; public bool hold_last; public bool in_place = true; public bool added; }

    static HashSet<string> AddedClips(Dictionary<string, SidecarClip> side) =>
        side == null ? new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                     : new HashSet<string>(side.Values.Where(c => c.added).Select(c => c.name), StringComparer.OrdinalIgnoreCase);
    [Serializable] class Sidecar { public int version; public string motion = ""; public List<SidecarClip> clips = new List<SidecarClip>(); }

    static Dictionary<string, SidecarClip> ReadClipInfo(string path, Report report)
    {
        if (path == null || !File.Exists(path)) return null;
        try
        {
            var doc = JsonUtility.FromJson<Sidecar>(File.ReadAllText(path));
            var map = new Dictionary<string, SidecarClip>(StringComparer.OrdinalIgnoreCase);
            foreach (var c in doc.clips ?? new List<SidecarClip>()) if (!string.IsNullOrEmpty(c.name)) map[c.name] = c;
            report.clipInfoUsed = map.Count > 0;
            return map;
        }
        catch (Exception e) { report.warnings.Add("clip info not readable: " + e.Message); return null; }
    }

    static void SetClipLoops(ModelImporter mi, Report report, Dictionary<string, SidecarClip> sidecar, HashSet<string> only = null)
    {
        var defs = mi.defaultClipAnimations;
        if (defs == null || defs.Length == 0) return;
        foreach (var d in defs)
        {
            if (only != null && !only.Contains(ShortName(d.name))) continue;
            var info = new ClipInfo { name = d.name, defaultLoop = d.loopTime };
            SidecarClip side = null;
            if (sidecar != null) sidecar.TryGetValue(ShortName(d.name), out side);
            bool cyclic = side != null ? side.loop : IsCyclic(d.name);
            bool inPlace = side != null ? side.in_place : !Words(d.name).Contains("rm");
            if (cyclic)
            {
                d.loopTime = true;
                d.loopPose = true;
                if (inPlace)
                {
                    // In-place cycle: bake root rotation, height and XZ into the pose, based on the original root
                    // (FaceForward may switch the rotation to the body's orientation after measuring it).
                    d.lockRootRotation = true; d.keepOriginalOrientation = true;
                    d.lockRootHeightY = true; d.keepOriginalPositionY = true;
                    d.lockRootPositionXZ = true; d.keepOriginalPositionXZ = true;
                    info.bakedInPlace = true;
                }
            }
            info.loop = d.loopTime;
            report.clips.Add(info);
        }
        mi.clipAnimations = defs;
        mi.SaveAndReimport();
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Facing (Version 16)

    public const string FacingMark = "vgac-facing-16-2";
    const float FacingRetryDeg = 10f;   // a baked loop further off than this is tried based upon the body's orientation
    const int FacingSamples = 24;

    // The body's heading in one pose: forward is (hip line + shoulder line) x up, in degrees about +Y from the root's
    // forward (+ to the right). False when the body lies on its side (the lines point mostly up or down).
    static bool BodyHeading(Animator an, out float deg)
    {
        deg = 0f;
        var lu = an.GetBoneTransform(HumanBodyBones.LeftUpperLeg); var ru = an.GetBoneTransform(HumanBodyBones.RightUpperLeg);
        var la = an.GetBoneTransform(HumanBodyBones.LeftUpperArm); var ra = an.GetBoneTransform(HumanBodyBones.RightUpperArm);
        if (lu == null || ru == null || la == null || ra == null) return false;
        var right = (ru.position - lu.position) + (ra.position - la.position);
        float full = right.magnitude;
        right.y = 0f;
        if (full < 1e-4f || right.magnitude < 0.5f * full) return false;
        var own = an.transform.forward; own.y = 0f;
        deg = Vector3.SignedAngle(own, Vector3.Cross(right, Vector3.up), Vector3.up);
        return true;
    }

    // The circular mean of the heading over FacingSamples poses of the move, and the widest swing from it. False when
    // fewer than a quarter of the poses could be read.
    static bool ClipHeading(Animator an, AnimationClip clip, out float mean, out float swing, out float lean)
    {
        mean = swing = lean = 0f;
        var hips = an.GetBoneTransform(HumanBodyBones.Hips); var head = an.GetBoneTransform(HumanBodyBones.Head);
        float leanSum = 0f; int leanN = 0;
        var graph = PlayableGraph.Create("vgac_facing");
        var got = new List<float>();
        try
        {
            graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
            var output = AnimationPlayableOutput.Create(graph, "anim", an);
            var p = AnimationClipPlayable.Create(graph, clip);
            output.SetSourcePlayable(p);
            graph.Play();
            for (int i = 0; i < FacingSamples; i++)
            {
                double t = clip.length * i / FacingSamples;
                p.SetTime(t); graph.Evaluate(0f); p.SetTime(t); graph.Evaluate(0f);
                if (BodyHeading(an, out var d)) got.Add(d);
                if (hips != null && head != null) { leanSum += Vector3.Angle(head.position - hips.position, Vector3.up); leanN++; }
            }
        }
        finally { graph.Destroy(); }
        if (got.Count < FacingSamples / 4) return false;
        var v = Vector2.zero;
        foreach (var d in got) v += new Vector2(Mathf.Cos(d * Mathf.Deg2Rad), Mathf.Sin(d * Mathf.Deg2Rad));
        float m = Mathf.Atan2(v.y, v.x) * Mathf.Rad2Deg;
        mean = m;
        lean = leanN > 0 ? leanSum / leanN : 0f;
        swing = got.Max(d => Mathf.Abs(Mathf.DeltaAngle(m, d)));
        return true;
    }

    // A copy of the model at the origin, unturned, in its bind pose, animated whether or not a camera sees it (culling
    // would leave the bones where they were). Null when the model has no valid Humanoid avatar.
    static GameObject FacingRig(string fbxPath, out Animator an)
    {
        an = null;
        var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
        var avatar = AssetDatabase.LoadAllAssetsAtPath(fbxPath).OfType<Avatar>().FirstOrDefault();
        if (model == null || avatar == null || !avatar.isHuman || !avatar.isValid) return null;
        var go = (GameObject)UnityEngine.Object.Instantiate(model);
        go.name = "vgac_facing";
        go.transform.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
        ResetToBindPose(go);
        an = go.GetComponent<Animator>() ?? go.AddComponent<Animator>();
        an.avatar = avatar;
        an.runtimeAnimatorController = null;
        an.applyRootMotion = false;
        an.cullingMode = AnimatorCullingMode.AlwaysAnimate;
        return go;
    }

    // Measure every Humanoid move in the importers' files on a copy of the model at fbxPath; a baked loop that faces
    // more than FacingRetryDeg away based upon its original root is tried based upon the body's orientation and keeps
    // the better one. Fills report.clipFacing (replacing earlier entries of the same files), facingMeasured and
    // bindFacing. Never throws.
    static void FaceForward(string fbxPath, IList<ModelImporter> importers, Report report)
    {
        GameObject rig = null;
        try
        {
            rig = FacingRig(fbxPath, out var an);
            if (rig == null) return;
            if (BodyHeading(an, out var bind)) report.bindFacing = bind;
            foreach (var mi in importers.Where(m => m != null).Distinct())
            {
                var path = mi.assetPath;
                string source = path == fbxPath ? "own" : "shared";
                var measured = new Dictionary<string, (bool ok, float mean, float swing, float lean)>();
                void Measure(IEnumerable<string> only)
                {
                    var want = only == null ? null : new HashSet<string>(only);
                    foreach (var c in LoadClips(path))
                        if (want == null || want.Contains(c.name))
                            measured[c.name] = ClipHeading(an, c, out var m, out var s, out var l) ? (true, m, s, l) : (false, 0f, 0f, 0f);
                }
                void Reimport()
                {
                    mi.SaveAndReimport();
                    if (path == fbxPath)  // the copy came from this file: make it again from the new import
                    {
                        UnityEngine.Object.DestroyImmediate(rig);
                        rig = FacingRig(fbxPath, out an);
                        if (rig == null) throw new Exception("the model lost its Humanoid avatar on re-import");
                    }
                }
                Measure(null);
                var defs = mi.clipAnimations;
                var tried = new List<int>();
                for (int i = 0; i < defs.Length; i++)
                {
                    var d = defs[i];
                    if (d.lockRootRotation && d.keepOriginalOrientation && measured.TryGetValue(d.name, out var r) && r.ok && Mathf.Abs(r.mean) > FacingRetryDeg)
                    {
                        d.keepOriginalOrientation = false;
                        tried.Add(i);
                    }
                }
                if (tried.Count > 0)
                {
                    var before = tried.ToDictionary(i => defs[i].name, i => measured[defs[i].name]);
                    mi.clipAnimations = defs;
                    Reimport();
                    Measure(before.Keys);
                    defs = mi.clipAnimations;
                    bool back = false;
                    foreach (var i in tried)
                    {
                        var nm = defs[i].name;
                        var now = measured[nm];
                        if (now.ok && Mathf.Abs(now.mean) < Mathf.Abs(before[nm].mean) - 1f) continue;
                        defs[i].keepOriginalOrientation = true;  // no better: back to the original root
                        measured[nm] = before[nm];
                        back = true;
                    }
                    if (back) { mi.clipAnimations = defs; Reimport(); }
                }
                var how = defs.ToDictionary(d => d.name, d => d.lockRootRotation ? (d.keepOriginalOrientation ? "original" : "body") : "");
                report.clipFacing.RemoveAll(f => f.source == source);
                foreach (var kv in measured.OrderBy(k => k.Key, StringComparer.Ordinal))
                    report.clipFacing.Add(new ClipFacing
                    {
                        name = kv.Key, source = source, measured = kv.Value.ok,
                        degrees = (float)Math.Round(kv.Value.mean, 1), swing = (float)Math.Round(kv.Value.swing, 1),
                        lean = (float)Math.Round(kv.Value.lean, 1),
                        basedOn = how.TryGetValue(kv.Key, out var h) ? h : "",
                    });
            }
            report.facingMeasured = true;
        }
        catch (Exception e)
        {
            report.facingMeasured = false;
            report.warnings.Add("move facing not measured: " + e.Message);
        }
        finally
        {
            if (rig != null) UnityEngine.Object.DestroyImmediate(rig);
        }
    }

    // The library's Humanoid mapping and skeleton are made again by Unity's auto-mapping on the next import. Needed
    // whenever the axis conversion changes: a skeleton stored from the import without it no longer matches the file
    // ("Avatar Rig Configuration mis-match") and every retargeted move comes out bent forward a quarter turn.
    static void ClearHumanDescription(ModelImporter ci)
    {
        var hd = ci.humanDescription;
        hd.human = new HumanBone[0];
        hd.skeleton = new SkeletonBone[0];
        ci.humanDescription = hd;
        ci.animationType = ModelImporterAnimationType.Human;
        ci.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
    }

    // Version 16: characters imported by an older importer still carry the library's import without axis conversion.
    // Each Assets/VGAC/<name>/clips.fbx without the facing mark is imported again with it on and the facing chosen as
    // a fresh import would (the controller keeps its clips: they are found by name). skipName is the character being
    // imported now (its import sets everything itself). -> the number of characters brought up to date. Never throws.
    public static int RefreshClipFacing(string skipName = null)
    {
        int n = 0;
        try
        {
            if (!Directory.Exists(RequestRoot)) return 0;
            foreach (var dir in Directory.GetDirectories(RequestRoot).Select(d => d.Replace('\\', '/')))
            {
                var name = Path.GetFileName(dir);
                if (name == skipName) continue;
                var clipsPath = dir + "/clips.fbx";
                var fbxPath = dir + "/" + name + ".fbx";
                if (!File.Exists(clipsPath) || !File.Exists(fbxPath)) continue;
                var ci = AssetImporter.GetAtPath(clipsPath) as ModelImporter;
                if (ci == null || ci.userData == FacingMark) continue;
                try
                {
                    var rep = new Report();
                    ci.bakeAxisConversion = true;
                    ci.userData = FacingMark;
                    ClearHumanDescription(ci);
                    // Loop and bake settings as SetClipLoops gives them, starting again from the original root.
                    var defs = ci.clipAnimations.Length > 0 ? ci.clipAnimations : ci.defaultClipAnimations;
                    foreach (var d in defs) if (d.lockRootRotation) d.keepOriginalOrientation = true;
                    ci.clipAnimations = defs;
                    ci.SaveAndReimport();
                    var mi = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
                    bool ownBaked = mi != null && mi.clipAnimations.Any(d => d.lockRootRotation);
                    FaceForward(fbxPath, ownBaked ? new[] { ci, mi } : new[] { ci }, rep);
                    var worst = rep.clipFacing.Where(f => f.measured).OrderByDescending(f => Mathf.Abs(f.degrees)).FirstOrDefault();
                    Debug.Log("VGAC: moves of " + name + " now face forward" + (worst != null ? " (furthest off: " + worst.name + ", " + worst.degrees.ToString("0") + " degrees)" : ""));
                    n++;
                }
                catch (Exception e) { Debug.LogWarning("VGAC: could not update the move facing of " + name + ": " + e.Message); }
            }
            if (n > 0) AssetDatabase.SaveAssets();
        }
        catch (Exception e) { Debug.LogWarning("VGAC: move facing update failed: " + e.Message); }
        return n;
    }

    [MenuItem("Tools/VGAC/Fix move facing")]
    static void RefreshClipFacingMenu()
    {
        int n = RefreshClipFacing();
        Debug.Log(n == 0 ? "VGAC: every character's moves already face forward." : "VGAC: updated the move facing of " + n + " character(s).");
    }

    static List<AnimationClip> LoadClips(string path) =>
        AssetDatabase.LoadAllAssetsAtPath(path).OfType<AnimationClip>().Where(c => !c.name.StartsWith("__preview")).ToList();

    // Best clip whose words include one of `want`: fewest extra words (Idle_Loop beats Sword_Idle), then shortest name.
    static AnimationClip Pick(List<AnimationClip> clips, HashSet<AnimationClip> used, params string[] want)
    {
        return clips.Where(c => !used.Contains(c))
            .Select(c => (c, w: Words(c.name)))
            .Where(x => x.w.Any(want.Contains) && !x.w.Contains("crouch") && !x.w.Contains("swim") && !x.w.Contains("sitting"))
            .OrderBy(x => x.w.Count(t => !want.Contains(t) && !NeutralWords.Contains(t)))
            .ThenBy(x => x.c.name.Length).ThenBy(x => x.c.name, StringComparer.Ordinal)
            .Select(x => x.c).FirstOrDefault();
    }

    static AnimatorController BuildController(string path, List<AnimationClip> clips, bool humanoid, float speedScale, ControllerInfo info,
                                              HashSet<string> added = null)
    {
        AssetDatabase.DeleteAsset(path);
        var ctrl = AnimatorController.CreateAnimatorControllerAtPath(path);
        var sm = ctrl.layers[0].stateMachine;
        var used = new HashSet<AnimationClip>();
        // Moves the person added get their own state and parameter below, never a role such as Attack.
        var extra = added == null ? new List<AnimationClip>() : clips.Where(c => added.Contains(ShortName(c.name))).ToList();
        foreach (var c in extra) used.Add(c);
        AnimationClip Take(AnimationClip c) { if (c != null) used.Add(c); return c; }

        var idle = Take(Pick(clips, used, "idle"));
        var walk = Take(Pick(clips, used, "walk") ?? Pick(clips, used, "slither"));
        var run = Take(Pick(clips, used, "run") ?? Pick(clips, used, "jog") ?? Pick(clips, used, "sprint"));
        var attack = Take(Pick(clips, used, "attack") ?? Pick(clips, used, "punch", "slash", "bite", "strike"));
        var hit = Take(Pick(clips, used, "hit", "hurt", "damage", "impact"));
        var die = Take(Pick(clips, used, "death", "die", "dying"));
        AnimationClip jump = null, jumpLand = null;
        if (humanoid)
        {
            jump = clips.FirstOrDefault(c => !used.Contains(c) && Words(c.name).SequenceEqual(new[] { "jump" }));
            if (jump == null)
            {
                jump = clips.FirstOrDefault(c => !used.Contains(c) && Words(c.name).Contains("jump") && Words(c.name).Contains("start"));
                jumpLand = clips.FirstOrDefault(c => !used.Contains(c) && Words(c.name).Contains("jump") && Words(c.name).Contains("land"));
            }
            Take(jump); Take(jumpLand);
        }
        AnimationClip Exact(params string[] words) => clips.FirstOrDefault(c => !used.Contains(c) && words.All(w => Words(c.name).Contains(w)));
        var turnLeft = Take(Exact("turn", "left"));
        var turnRight = Take(Exact("turn", "right"));
        var flap = Take(Pick(clips, used, "flap"));
        AnimationClip glide = null, takeoff = null, land = null;
        if (flap)
        {
            glide = Take(Pick(clips, used, "glide"));
            takeoff = Take(Pick(clips, used, "takeoff") ?? Exact("take", "off"));
            land = Take(Pick(clips, used, "land", "landing"));
        }
        foreach (var (role, c) in new[] { ("Idle", idle), ("Walk", walk), ("Run", run), ("Attack", attack), ("Hit", hit), ("Die", die), ("Jump", jump), ("JumpLand", jumpLand),
                                          ("TurnLeft", turnLeft), ("TurnRight", turnRight), ("Flap", flap), ("Glide", glide), ("Takeoff", takeoff), ("Land", land) })
            if (c != null) info.roles.Add(role + "=" + c.name);

        AnimatorState loco = null;
        var cycle = new List<(AnimationClip clip, float speed)>();
        if (idle) cycle.Add((idle, 0f));
        if (walk) cycle.Add((walk, 1.4f * speedScale));
        if (run) cycle.Add((run, 3.5f * speedScale));
        if (cycle.Count >= 2)
        {
            ctrl.AddParameter("Speed", AnimatorControllerParameterType.Float);
            var tree = new BlendTree
            {
                name = "Locomotion",
                blendType = BlendTreeType.Simple1D,
                blendParameter = "Speed",
                useAutomaticThresholds = false,
                hideFlags = HideFlags.HideInHierarchy,
            };
            AssetDatabase.AddObjectToAsset(tree, ctrl);
            foreach (var (clip, speed) in cycle) { tree.AddChild(clip, speed); info.locomotion.Add(clip.name + "@" + speed.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture)); }
            loco = sm.AddState("Locomotion", new Vector3(300, 0, 0));
            loco.motion = tree;
        }
        else if (cycle.Count == 1)
        {
            loco = sm.AddState("Locomotion", new Vector3(300, 0, 0));
            loco.motion = cycle[0].clip;
            info.locomotion.Add(cycle[0].clip.name + "@0");
        }

        var actionStates = new List<AnimatorState>();
        if (loco != null)
        {
            sm.defaultState = loco;
            AnimatorState OneShot(string state, string trigger, AnimationClip clip, Vector3 pos, IEnumerable<AnimatorState> from)
            {
                if (ctrl.parameters.All(p => p.name != trigger)) ctrl.AddParameter(trigger, AnimatorControllerParameterType.Trigger);
                var st = sm.AddState(state, pos);
                st.motion = clip;
                foreach (var src in from)
                {
                    var t = src.AddTransition(st);
                    t.AddCondition(AnimatorConditionMode.If, 0, trigger);
                    t.hasExitTime = false;
                    t.duration = 0.1f;
                }
                return st;
            }
            void Back(AnimatorState st, AnimatorState to)
            {
                var b = st.AddTransition(to);
                b.hasExitTime = true;
                b.exitTime = 0.9f;
                b.duration = 0.1f;
            }
            AnimatorState attackSt = null, jumpSt = null, landSt = null, hitSt = null;
            if (attack) { attackSt = OneShot("Attack", "Attack", attack, new Vector3(600, -120, 0), new[] { loco }); Back(attackSt, loco); actionStates.Add(attackSt); }
            if (jump)
            {
                jumpSt = OneShot("Jump", "Jump", jump, new Vector3(600, 120, 0), new[] { loco });
                actionStates.Add(jumpSt);
                if (jumpLand)
                {
                    landSt = sm.AddState("Jump Land", new Vector3(850, 120, 0));
                    landSt.motion = jumpLand;
                    Back(jumpSt, landSt);
                    Back(landSt, loco);
                    actionStates.Add(landSt);
                }
                else Back(jumpSt, loco);
            }
            if (turnLeft) { var st = OneShot("Turn Left", "TurnLeft", turnLeft, new Vector3(600, 240, 0), new[] { loco }); Back(st, loco); actionStates.Add(st); }
            if (turnRight) { var st = OneShot("Turn Right", "TurnRight", turnRight, new Vector3(600, 360, 0), new[] { loco }); Back(st, loco); actionStates.Add(st); }
            if (flap)
            {
                // Flying: Bool "Flying"; Locomotion -> Takeoff -> Fly while true, Fly -> Land -> Locomotion when false.
                ctrl.AddParameter("Flying", AnimatorControllerParameterType.Bool);
                var fly = sm.AddState("Fly", new Vector3(300, -250, 0));
                if (glide)
                {
                    if (ctrl.parameters.All(p => p.name != "Speed")) ctrl.AddParameter("Speed", AnimatorControllerParameterType.Float);
                    var ftree = new BlendTree { name = "Fly", blendType = BlendTreeType.Simple1D, blendParameter = "Speed", useAutomaticThresholds = false, hideFlags = HideFlags.HideInHierarchy };
                    AssetDatabase.AddObjectToAsset(ftree, ctrl);
                    ftree.AddChild(flap, 0f);
                    ftree.AddChild(glide, 3.5f * speedScale);
                    fly.motion = ftree;
                }
                else fly.motion = flap;
                AnimatorStateTransition When(AnimatorState a, AnimatorState b, bool flying)
                {
                    var t = a.AddTransition(b);
                    t.AddCondition(flying ? AnimatorConditionMode.If : AnimatorConditionMode.IfNot, 0, "Flying");
                    t.hasExitTime = false; t.duration = 0.15f;
                    return t;
                }
                if (takeoff) { var st = sm.AddState("Takeoff", new Vector3(50, -250, 0)); st.motion = takeoff; When(loco, st, true); Back(st, fly); actionStates.Add(st); }
                else When(loco, fly, true);
                if (land) { var st = sm.AddState("Land", new Vector3(550, -250, 0)); st.motion = land; When(fly, st, false); Back(st, loco); actionStates.Add(st); }
                else When(fly, loco, false);
                actionStates.Add(fly);
            }
            if (hit)
            {
                var from = new List<AnimatorState> { loco };
                if (attackSt != null) from.Add(attackSt);
                hitSt = OneShot("Hit", "Hit", hit, new Vector3(600, 0, 0), from);
                Back(hitSt, loco);
                actionStates.Add(hitSt);
            }
            // Added moves: a trigger per one-off move, a bool per looping one (true to play it, false to go back),
            // named after the move: "sword_block" -> SetTrigger("SwordBlock").
            for (int k = 0; k < extra.Count; k++)
            {
                var c = extra[k];
                var param = UniqueParam(ctrl, Pascal(ShortName(c.name)));
                var pos = new Vector3(900, 240 + 60 * k, 0);
                AnimatorState st;
                if (AnimationUtility.GetAnimationClipSettings(c).loopTime)
                {
                    ctrl.AddParameter(param, AnimatorControllerParameterType.Bool);
                    st = sm.AddState(UniqueState(sm, ShortName(c.name)), pos);
                    st.motion = c;
                    var t = loco.AddTransition(st); t.AddCondition(AnimatorConditionMode.If, 0, param); t.hasExitTime = false; t.duration = 0.15f;
                    var b = st.AddTransition(loco); b.AddCondition(AnimatorConditionMode.IfNot, 0, param); b.hasExitTime = false; b.duration = 0.15f;
                    info.added.Add(param + ":Bool");
                }
                else
                {
                    st = OneShot(UniqueState(sm, ShortName(c.name)), param, c, pos, new[] { loco });
                    Back(st, loco);
                    info.added.Add(param + ":Trigger");
                }
                actionStates.Add(st);
            }
            if (die)
            {
                var from = new List<AnimatorState> { loco };
                from.AddRange(actionStates);
                OneShot("Dead", "Die", die, new Vector3(900, -120, 0), from);  // no way out
            }
        }
        else
        {
            // No idle, walk or run: nothing to blend; every clip becomes a plain state as before.
            foreach (var c in clips.Where(c => !used.Contains(c)).Concat(used)) { var st = sm.AddState(UniqueState(sm, ShortName(c.name))); st.motion = c; }
            used = new HashSet<AnimationClip>(clips);
        }

        // Every other clip is available in "More moves", not connected.
        var rest = clips.Where(c => !used.Contains(c)).Take(200).ToList();
        if (rest.Count > 0)
        {
            var more = sm.AddStateMachine("More moves", new Vector3(300, 250, 0));
            int i = 0;
            foreach (var c in rest)
            {
                var st = more.AddState(UniqueState(more, ShortName(c.name)), new Vector3(250 + 250 * (i % 4), 60 * (i / 4), 0));
                st.motion = c;
                i++;
            }
            info.moreMoves = rest.Count;
        }

        info.path = path;
        info.states = sm.states.Select(s => s.state.name).ToList();
        if (rest.Count > 0) info.states.Add("More moves");
        info.parameters = ctrl.parameters.Select(p => p.name + ":" + p.type).ToList();
        EditorUtility.SetDirty(ctrl);
        AssetDatabase.SaveAssets();
        return ctrl;
    }

    // "sword_block" -> "SwordBlock"; a parameter name the controller does not have yet.
    static string Pascal(string name) =>
        string.Concat(name.Split(new[] { '_', ' ', '-', '.' }, StringSplitOptions.RemoveEmptyEntries)
                          .Select(w => char.ToUpperInvariant(w[0]) + w.Substring(1)));

    static string UniqueParam(AnimatorController ctrl, string name)
    {
        if (string.IsNullOrEmpty(name)) name = "Move";
        var n = name; int k = 2;
        while (ctrl.parameters.Any(p => p.name == n)) n = name + k++;
        return n;
    }

    static string UniqueState(AnimatorStateMachine sm, string name)
    {
        var n = name; int k = 2;
        while (sm.states.Any(s => s.state.name == n)) n = name + " " + k++;
        return n;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Showcase scene (PHOTOREAL-PLAN phase 1, "Preview in realistic light"). Assets/VGAC/_Showcase/VGAC Showcase.unity:
    // the outdoor light picture the app viewer uses (a CC0 Poly Haven HDRI, passed as -vgacHdri and copied in once) as
    // the sky, the ambient light and what metal reflects, a sun matched to it with soft shadows, a plain ground and a
    // camera. The last imported asset stands in the "Showcased asset" slot. Only this scene is ever written: it is
    // opened next to whatever is open and closed again, so a person's own scenes are never touched. Without a light
    // picture the built-in procedural sky stands in. The snapshot (Snapshot below) is rendered in this scene.
    public const string ShowcaseDir = "Assets/VGAC/_Showcase";
    public const string ShowcaseScene = ShowcaseDir + "/VGAC Showcase.unity";
    const string ShowcaseSky = ShowcaseDir + "/VGAC Sky.hdr";
    const string ShowcaseSlot = "Showcased asset";
    // Measured once from the Outdoors light picture (kloofendal_48d_partly_cloudy_1k.hdr): average linear colour of the
    // sky, the horizon band and the ground, with the sun clipped out (it comes from the Sun light instead).
    static readonly Color ShowcaseSkyAmbient = new Color(0.41f, 0.48f, 0.70f);
    static readonly Color ShowcaseEquatorAmbient = new Color(0.25f, 0.27f, 0.34f);
    static readonly Color ShowcaseGroundAmbient = new Color(0.19f, 0.13f, 0.07f);
    // The camera looks at the asset's front from 30 degrees to its right; the sun comes from 60 degrees left of the
    // camera, 45 degrees up (front-left light, as in the app's beauty picture).
    static readonly Vector3 ShowcaseCamDir = new Vector3(0.5f, 0.2f, 0.85f).normalized;
    const float ShowcaseSunAzimuth = 90f, ShowcaseSunElevation = 45f;

    // GetComponent returns Unity's fake null, which ?? does not see.
    static T Comp<T>(GameObject g) where T : Component { var c = g.GetComponent<T>(); return c != null ? c : g.AddComponent<T>(); }

    static GameObject RootIn(UnityEngine.SceneManagement.Scene s, string n) => s.GetRootGameObjects().FirstOrDefault(g => g.name == n);

    static Cubemap ShowcaseCubemap(string hdri)
    {
        Directory.CreateDirectory(ShowcaseDir);
        // Copied in when missing or when the app sends a different picture (a newer sky).
        if (!string.IsNullOrEmpty(hdri) && File.Exists(hdri) && (!File.Exists(ShowcaseSky) || !File.ReadAllBytes(hdri).SequenceEqual(File.ReadAllBytes(ShowcaseSky))))
        {
            File.Copy(hdri, ShowcaseSky, true);
            AssetDatabase.ImportAsset(ShowcaseSky, ImportAssetOptions.ForceSynchronousImport);
        }
        if (!File.Exists(ShowcaseSky)) return null;
        var ti = AssetImporter.GetAtPath(ShowcaseSky) as TextureImporter;
        if (ti != null)
        {
            var st = new TextureImporterSettings();
            ti.ReadTextureSettings(st);
            // Blurred like a rough surface sees it; a plain mip chain spreads the sun into a bright sheen on cloth.
            if (st.textureShape != TextureImporterShape.TextureCube || st.cubemapConvolution != TextureImporterCubemapConvolution.Specular)
            {
                st.textureShape = TextureImporterShape.TextureCube;
                st.generateCubemap = TextureImporterGenerateCubemap.AutoCubemap;
                st.cubemapConvolution = TextureImporterCubemapConvolution.Specular;
                st.mipmapEnabled = true;
                ti.SetTextureSettings(st);
                ti.SaveAndReimport();
            }
        }
        return AssetDatabase.LoadAssetAtPath<Cubemap>(ShowcaseSky);
    }

    static Material ShowcaseMaterial(string path, Shader shader)
    {
        var m = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (m == null) { m = new Material(shader); AssetDatabase.CreateAsset(m, path); }
        else if (m.shader != shader) m.shader = shader;
        return m;
    }

    // Sets up the showcase's light, ground and camera in scene s (idempotent; objects are found by name).
    static void DressShowcase(UnityEngine.SceneManagement.Scene s, string hdri)
    {
        var cube = ShowcaseCubemap(hdri);
        var prev = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
        UnityEngine.SceneManagement.SceneManager.SetActiveScene(s);
        try
        {
            var sky = ShowcaseMaterial(ShowcaseDir + "/VGAC Sky.mat", Shader.Find(cube != null ? "Skybox/Cubemap" : "Skybox/Procedural"));
            if (cube != null)
            {
                sky.SetTexture("_Tex", cube);
                sky.SetFloat("_Exposure", 1f);
                sky.SetFloat("_Rotation", 0f);
            }
            EditorUtility.SetDirty(sky);
            RenderSettings.skybox = sky;
            // Colours in RenderSettings are entered as the Inspector shows them (sRGB), whatever the project's colour space.
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = ShowcaseSkyAmbient.gamma;
            RenderSettings.ambientEquatorColor = ShowcaseEquatorAmbient.gamma;
            RenderSettings.ambientGroundColor = ShowcaseGroundAmbient.gamma;
            RenderSettings.ambientIntensity = 0.6f;
            if (cube != null)
            {
                RenderSettings.defaultReflectionMode = DefaultReflectionMode.Custom;
                RenderSettings.customReflectionTexture = cube;
            }
            RenderSettings.reflectionIntensity = 0.6f;
            RenderSettings.fog = false;

            var sunGo = RootIn(s, "Sun") ?? new GameObject("Sun");
            var sun = Comp<Light>(sunGo);
            sun.type = LightType.Directional;
            sun.color = new Color(1f, 0.95f, 0.88f);
            sun.intensity = 1.0f;
            sun.shadows = LightShadows.Soft;
            sun.shadowStrength = 0.85f;
            float az = ShowcaseSunAzimuth * Mathf.Deg2Rad, el = ShowcaseSunElevation * Mathf.Deg2Rad;
            var toSun = new Vector3(Mathf.Sin(az) * Mathf.Cos(el), Mathf.Sin(el), Mathf.Cos(az) * Mathf.Cos(el));
            sunGo.transform.rotation = Quaternion.LookRotation(-toSun);
            RenderSettings.sun = sun;

            var ground = RootIn(s, "Ground");
            if (ground == null)
            {
                ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
                ground.name = "Ground";
            }
            var gm = ShowcaseMaterial(ShowcaseDir + "/VGAC Ground.mat", Shader.Find("Standard") ?? Shader.Find("Universal Render Pipeline/Lit"));
            gm.color = new Color(0.2f, 0.2f, 0.21f);
            if (gm.HasProperty("_Glossiness")) gm.SetFloat("_Glossiness", 0.15f);
            if (gm.HasProperty("_Smoothness")) gm.SetFloat("_Smoothness", 0.15f);
            EditorUtility.SetDirty(gm);
            ground.GetComponent<Renderer>().sharedMaterial = gm;

            var camGo = RootIn(s, "Showcase camera") ?? new GameObject("Showcase camera");
            var cam = Comp<Camera>(camGo);
            cam.clearFlags = CameraClearFlags.Skybox;
            cam.fieldOfView = 30f;
            camGo.tag = "MainCamera";
            if (RootIn(s, ShowcaseSlot) == null) new GameObject(ShowcaseSlot);
        }
        finally
        {
            if (prev.IsValid() && prev != s) UnityEngine.SceneManagement.SceneManager.SetActiveScene(prev);
        }
    }

    // Puts the camera where it frames everything under `target` (three-quarter front, a little above), and the
    // ground just under it. Returns the distance.
    static float FrameShowcase(Camera cam, GameObject target, GameObject ground)
    {
        var rs = target.GetComponentsInChildren<Renderer>().Where(r => r.enabled).ToList();
        var b = rs.Count > 0 ? rs[0].bounds : new Bounds(target.transform.position, Vector3.one);
        foreach (var r in rs) b.Encapsulate(r.bounds);
        float radius = Mathf.Max(b.extents.magnitude, 0.05f);
        float dist = radius / Mathf.Sin(cam.fieldOfView * 0.5f * Mathf.Deg2Rad) * 1.02f;
        cam.transform.position = b.center + ShowcaseCamDir * dist;
        cam.transform.LookAt(b.center);
        cam.nearClipPlane = Mathf.Max(0.01f, dist / 1000f);
        cam.farClipPlane = dist * 50f;
        if (ground != null)
        {
            ground.transform.position = new Vector3(b.center.x, b.min.y - 0.002f, b.center.z);
            float s = Mathf.Max(20f, radius * 20f);
            ground.transform.localScale = new Vector3(s, 1f, s);
        }
        return dist;
    }

    // Opens the showcase scene next to whatever is open, creating and dressing it as needed. openedHere says whether
    // the caller should close it again.
    static UnityEngine.SceneManagement.Scene OpenShowcase(string hdri, out bool openedHere)
    {
        var s = UnityEngine.SceneManagement.SceneManager.GetSceneByPath(ShowcaseScene);
        openedHere = !(s.IsValid() && s.isLoaded);
        if (openedHere)
        {
            Directory.CreateDirectory(ShowcaseDir);
            if (File.Exists(ShowcaseScene))
                s = UnityEditor.SceneManagement.EditorSceneManager.OpenScene(ShowcaseScene, UnityEditor.SceneManagement.OpenSceneMode.Additive);
            else
            {
                // Unity cannot add a new scene next to an unsaved untitled one. In batch mode nothing is lost by
                // replacing it; in an open Editor the person is asked to save their scene first.
                bool untitled = Enumerable.Range(0, UnityEngine.SceneManagement.SceneManager.sceneCount)
                    .Any(i => string.IsNullOrEmpty(UnityEngine.SceneManagement.SceneManager.GetSceneAt(i).path));
                if (untitled && !Application.isBatchMode) throw new Exception("save the open scene once, then import again to make the showcase scene");
                s = UnityEditor.SceneManagement.EditorSceneManager.NewScene(UnityEditor.SceneManagement.NewSceneSetup.EmptyScene,
                    untitled ? UnityEditor.SceneManagement.NewSceneMode.Single : UnityEditor.SceneManagement.NewSceneMode.Additive);
                UnityEditor.SceneManagement.EditorSceneManager.SaveScene(s, ShowcaseScene);
            }
        }
        DressShowcase(s, hdri);
        return s;
    }

    // Stands the prefab in the showcase's slot (replacing the last one), frames the camera on it and saves the scene.
    // Returns the scene path, or "" when there is nothing to show. Never throws.
    public static string PlaceInShowcase(string prefabPath, string hdri, List<string> warnings)
    {
        UnityEngine.SceneManagement.Scene s = default;
        bool openedHere = false;
        try
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            if (prefab == null) return "";
            s = OpenShowcase(hdri, out openedHere);
            var slot = RootIn(s, ShowcaseSlot);
            for (int i = slot.transform.childCount - 1; i >= 0; i--) UnityEngine.Object.DestroyImmediate(slot.transform.GetChild(i).gameObject);
            var inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab, slot.transform);
            inst.transform.localPosition = Vector3.zero;
            inst.transform.localRotation = Quaternion.identity;
            ResetToBindPose(inst);
            var cam = RootIn(s, "Showcase camera").GetComponent<Camera>();
            FrameShowcase(cam, inst, RootIn(s, "Ground"));
            UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(s);
            UnityEditor.SceneManagement.EditorSceneManager.SaveScene(s);
            AssetDatabase.SaveAssets();
            return ShowcaseScene;
        }
        catch (Exception e)
        {
            warnings?.Add("showcase scene not updated: " + e.Message);
            return "";
        }
        finally
        {
            if (openedHere && s.IsValid() && UnityEngine.SceneManagement.SceneManager.sceneCount > 1)
                UnityEditor.SceneManagement.EditorSceneManager.CloseScene(s, true);
        }
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Render frames of the prefab's clips to PNG without entering play mode.
    // Run: Unity.exe -batchmode -quit -projectPath <sandbox> -executeMethod VgacImport.Snapshot -vgacName <name> -vgacOut <dir>
    //      [-vgacClipFilter walk,idle] [-vgacFrames 4] [-vgacSize 512]
    // Needs graphics (do not pass -nographics). Clips are evaluated through a PlayableGraph, which honours Loop Time
    // and Humanoid retargeting; each clip also gets one probe frame at 1.5 times its length: a looping clip shows a
    // different pose there than at frame 0 (snapshot_report.json, loopProbes, metres the bones moved).
    public static void Snapshot()
    {
        var name = Arg("-vgacName");
        var outDir = Arg("-vgacOut") ?? ".";
        var filter = (Arg("-vgacClipFilter") ?? "").ToLowerInvariant().Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        int frames = int.Parse(Arg("-vgacFrames") ?? "4");
        int size = int.Parse(Arg("-vgacSize") ?? "512");
        var log = new List<string>();
        var probes = new List<string>();
        try
        {
            Directory.CreateDirectory(outDir);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/VGAC/" + name + "/" + name + ".prefab");
            if (prefab == null) throw new Exception("prefab missing for " + name);
            // Rendered in the showcase scene (its light, sky, reflections and ground), with its own slot hidden.
            var hdri = Arg("-vgacHdri");
            var made = OpenShowcase(hdri, out var openedHere);
            UnityEditor.SceneManagement.EditorSceneManager.SaveScene(made);
            var scene = UnityEditor.SceneManagement.EditorSceneManager.OpenScene(ShowcaseScene, UnityEditor.SceneManagement.OpenSceneMode.Single);
            var slot = RootIn(scene, ShowcaseSlot);
            if (slot != null) slot.SetActive(false);
            var showcaseCam = RootIn(scene, "Showcase camera");
            if (showcaseCam != null) showcaseCam.SetActive(false);
            var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            var camGo = new GameObject("snapcam");
            var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.Skybox;
            cam.fieldOfView = 30f;
            FrameShowcase(cam, go, RootIn(scene, "Ground"));
            var rt = new RenderTexture(size, size, 24);
            cam.targetTexture = rt;
            var animator = go.GetComponent<Animator>();
            // Version 16: the imported Animator culls by renderer visibility, so an evaluation before the camera has
            // drawn the model left the bones where they were (the strip's first frame showed the bind pose, not the
            // move). The snapshot copy always animates.
            if (animator != null) animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            var clips = animator != null && animator.runtimeAnimatorController != null ? animator.runtimeAnimatorController.animationClips.Distinct().ToArray() : new AnimationClip[0];
            var picked = clips.Select(c => (c, idx: filter.Length == 0 ? 0 : Array.FindIndex(filter, f => c.name.ToLowerInvariant().Contains(f))))
                .Where(x => x.idx >= 0)
                .OrderBy(x => x.idx).ThenBy(x => Words(x.c.name).Count(t => !NeutralWords.Contains(t))).ThenBy(x => x.c.name.Length)
                .Select(x => x.c).Take(6).ToList();
            if (picked.Count == 0) picked.Add(null);
            var boneTs = go.GetComponentsInChildren<Transform>(true);

            void Shoot(string file)
            {
                cam.Render();
                RenderTexture.active = rt;
                var tex = new Texture2D(size, size, TextureFormat.RGB24, false);
                tex.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                tex.Apply();
                RenderTexture.active = null;
                File.WriteAllBytes(file, tex.EncodeToPNG());
                log.Add(file);
            }

            foreach (var clip in picked)
            {
                if (clip == null) { Shoot(Path.Combine(outDir, "rest_00.png")); continue; }
                var graph = PlayableGraph.Create("vgac_snapshot");
                graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                var output = AnimationPlayableOutput.Create(graph, "anim", animator);
                var playable = AnimationClipPlayable.Create(graph, clip);
                output.SetSourcePlayable(playable);
                graph.Play();
                var clipName = clip.name.Replace("|", "_").Replace(" ", "_");
                Vector3[] Sample(double t)
                {
                    // Evaluate twice: the first evaluation of a fresh Humanoid graph can still show the bind pose.
                    playable.SetTime(t);
                    graph.Evaluate(0f);
                    playable.SetTime(t);
                    graph.Evaluate(0f);
                    return boneTs.Select(b => b.position).ToArray();
                }
                Sample(0);  // warm-up: the first evaluation after the graph is built can still show the bind pose
                var lf = animator.isHuman ? animator.GetBoneTransform(HumanBodyBones.LeftFoot) : null;
                var rf = animator.isHuman ? animator.GetBoneTransform(HumanBodyBones.RightFoot) : null;
                var strides = new List<string>();
                Vector3[] first = null;
                for (int i = 0; i < frames; i++)
                {
                    var pose = Sample(clip.length * i / Math.Max(1, frames));
                    if (i == 0) first = pose;
                    if (lf && rf) strides.Add((lf.position - rf.position).magnitude.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));
                    Shoot(Path.Combine(outDir, $"{clipName}_{i:00}.png"));
                }
                var probe = Sample(clip.length * 1.5);
                Shoot(Path.Combine(outDir, $"{clipName}_x1.5.png"));
                float moved = 0f;
                for (int b = 0; b < probe.Length; b++) moved = Mathf.Max(moved, (probe[b] - first[b]).magnitude);
                probes.Add(clip.name + " loop=" + clip.isLooping + " moved_m=" + moved.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture)
                           + (strides.Count > 0 ? " feet_apart_m=" + string.Join("/", strides) : ""));
                graph.Destroy();
            }
            // Version 17, only when asked (-vgacHeadPitch on; the export never asks): how far the shared moves bow the head
            if ((Arg("-vgacHeadPitch") ?? "off").ToLowerInvariant() == "on") HeadPitchProbe(go, animator, clips, probes);
            // Version 15: walk and run from the side with the spring script stepping, and the same frames without it
            SpringShots(go, cam, outDir, log, probes, clips);
            // Version 13: the hair close up, with its own shader and with the body's, under two light directions
            HairShots(go, name, cam, outDir, log, probes);
            // Leave the showcase as it was, with this asset standing in its slot, and save it.
            UnityEngine.Object.DestroyImmediate(go);
            UnityEngine.Object.DestroyImmediate(camGo);
            if (slot != null) slot.SetActive(true);
            if (showcaseCam != null) showcaseCam.SetActive(true);
            var snapWarnings = new List<string>();
            PlaceInShowcase("Assets/VGAC/" + name + "/" + name + ".prefab", hdri, snapWarnings);
            File.WriteAllText(Path.Combine(outDir, "snapshot_report.json"), JsonUtility.ToJson(new SnapReport { ok = true, files = log, loopProbes = probes, showcase = ShowcaseScene, warnings = snapWarnings }, true));
            Debug.Log("VGAC_SNAPSHOT ok " + log.Count);
        }
        catch (Exception e)
        {
            File.WriteAllText(Path.Combine(outDir, "snapshot_report.json"), JsonUtility.ToJson(new SnapReport { ok = false, error = e.ToString(), files = log, loopProbes = probes }, true));
            Debug.Log("VGAC_SNAPSHOT fail " + e);
            EditorApplication.Exit(1);
        }
    }

    // Version 17, diagnostics only (-vgacHeadPitch on with Snapshot): for idle, walk and run, the average over 12 frames
    // of how far the head is turned forward from its bind pose, about the character's side axis, in degrees: "head" in
    // the world and "head_on_body" relative to the hips (the bow of the neck and head alone). In loopProbes.
    static void HeadPitchProbe(GameObject go, Animator animator, AnimationClip[] clips, List<string> notes)
    {
        if (animator == null || !animator.isHuman) return;
        var head = animator.GetBoneTransform(HumanBodyBones.Head);
        var hips = animator.GetBoneTransform(HumanBodyBones.Hips);
        if (head == null || hips == null) return;
        ResetToBindPose(go);
        var headBind = head.rotation; var hipsBind = hips.rotation;
        var fwd = go.transform.forward; var up = go.transform.up; var side = go.transform.right;
        float Pitch(Quaternion rel)
        {
            var u = rel * up;
            return Mathf.Atan2(Vector3.Dot(u, fwd), Vector3.Dot(u, up)) * Mathf.Rad2Deg;
        }
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        foreach (var want in new[] { "idle", "walk", "run" })
        {
            var clip = clips.Where(c => Words(c.name).Contains(want) && !c.name.ToLowerInvariant().Contains("crouch")).OrderBy(c => c.name.Length).FirstOrDefault();
            if (clip == null) continue;
            var graph = PlayableGraph.Create("vgac_headpitch");
            graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
            var output = AnimationPlayableOutput.Create(graph, "anim", animator);
            var playable = AnimationClipPlayable.Create(graph, clip);
            output.SetSourcePlayable(playable);
            graph.Play();
            float sw = 0f, sb = 0f;
            for (int i = 0; i < 12; i++)
            {
                double t = clip.length * i / 12.0;
                playable.SetTime(t); graph.Evaluate(0f); playable.SetTime(t); graph.Evaluate(0f);
                var relHead = head.rotation * Quaternion.Inverse(headBind);
                var relHips = hips.rotation * Quaternion.Inverse(hipsBind);
                sw += Pitch(relHead);
                sb += Pitch(Quaternion.Inverse(relHips) * relHead);
            }
            notes.Add("headpitch " + clip.name + " head_deg=" + (sw / 12f).ToString("0.0", inv) + " head_on_body_deg=" + (sb / 12f).ToString("0.0", inv));
            graph.Destroy();
        }
        ResetToBindPose(go);
    }

    // Spring bones (Version 15): for each of walk and run, the clip is played twice through at 30 frames a second with
    // the spring script stepped after each pose; the second pass is shot at 6 frames from the character's side, then
    // the same frames again with the script off (the chains at rest). In loopProbes: per clip, the most a chain tip
    // trailed from where its rest pose would put it, with the script on.
    static void SpringShots(GameObject go, Camera cam, string outDir, List<string> log, List<string> notes, AnimationClip[] clips)
    {
        var type = SpringType();
        var comp = type != null ? go.GetComponent(type) : null;
        var animator = go.GetComponent<Animator>();
        if (comp == null || animator == null) return;
        var step = type.GetMethod("Step");
        var tips = type.GetMethod("TipOffsets");
        var simField = type.GetField("simulate");
        var oldPos = cam.transform.position; var oldRot = cam.transform.rotation;
        var r0 = go.GetComponentsInChildren<Renderer>(true).FirstOrDefault(r => r.name.EndsWith("_LOD0")) ?? go.GetComponentInChildren<Renderer>();
        var b = r0 != null ? r0.bounds : new Bounds(go.transform.position + Vector3.up, Vector3.one * 2);
        float h = Mathf.Max(b.size.y, 0.5f);
        var centre = b.center;
        var side = go.transform.right;
        cam.transform.position = centre + side * (h * 0.6f / Mathf.Tan(cam.fieldOfView * 0.5f * Mathf.Deg2Rad));
        cam.transform.LookAt(centre);
        int size = cam.targetTexture != null ? cam.targetTexture.width : 384;
        foreach (var want in new[] { "walk", "run" })
        {
            var clip = clips.Where(c => (Words(c.name).Contains(want) || (want == "run" && Words(c.name).Contains("jog"))) && !c.name.ToLowerInvariant().Contains("crouch"))
                .OrderBy(c => c.name.Length).FirstOrDefault();
            if (clip == null) continue;
            var graph = PlayableGraph.Create("vgac_springs");
            graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
            var output = AnimationPlayableOutput.Create(graph, "anim", animator);
            var playable = AnimationClipPlayable.Create(graph, clip);
            output.SetSourcePlayable(playable);
            graph.Play();
            float fps = 30f;
            int n = Mathf.Max(6, Mathf.RoundToInt(clip.length * fps));
            int every = Math.Max(1, n / 6);
            float most = 0f;
            foreach (var on in new[] { true, false })
            {
                simField.SetValue(comp, on);
                ((MonoBehaviour)comp).enabled = false; ((MonoBehaviour)comp).enabled = true;  // fresh start
                for (int pass = 0; pass < 2; pass++)
                    for (int i = 0; i <= n; i++)
                    {
                        double t = clip.length * i / n;
                        playable.SetTime(t); graph.Evaluate(0f); playable.SetTime(t); graph.Evaluate(0f);
                        if (on) step.Invoke(comp, new object[] { 1f / fps });
                        else
                        {
                            // off: the chains at their rest under the animated parent
                            foreach (var ch in (Array)type.GetField("chains").GetValue(comp))
                            {
                                var bones = (Transform[])ch.GetType().GetField("bones").GetValue(ch);
                                var rest = (Quaternion[])ch.GetType().GetField("restLocal").GetValue(ch);
                                for (int k = 0; k < bones.Length; k++) bones[k].localRotation = rest[k];
                            }
                        }
                        if (on && pass == 1) foreach (var f in (float[])tips.Invoke(comp, null)) most = Mathf.Max(most, f);
                        if (pass == 1 && i % every == 0 && i / every < 6)
                        {
                            cam.Render();
                            RenderTexture.active = cam.targetTexture;
                            var tex = new Texture2D(size, size, TextureFormat.RGB24, false);
                            tex.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                            tex.Apply();
                            RenderTexture.active = null;
                            var file = Path.Combine(outDir, "springs_" + (on ? "on" : "off") + "_" + want + "_" + (i / every).ToString("00") + ".png");
                            File.WriteAllBytes(file, tex.EncodeToPNG());
                            UnityEngine.Object.DestroyImmediate(tex);
                            log.Add(file);
                        }
                    }
            }
            simField.SetValue(comp, true);
            notes.Add("springs " + clip.name + " tip_trail_m=" + most.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture));
            graph.Destroy();
        }
        cam.transform.SetPositionAndRotation(oldPos, oldRot);
    }

    // The head close up for the hair (Version 13): hair_<shader>_<light>.png, 768 px, the instance in its bind pose. The
    // shader is the hair material's own (vgac when it is VGAC/Hair) and the body's shader with the hair textures (lit).
    static void HairShots(GameObject go, string name, Camera cam, string outDir, List<string> log, List<string> notes)
    {
        var r0 = go.GetComponentsInChildren<Renderer>(true).FirstOrDefault(r => r.name.EndsWith("_LOD0") && r.sharedMaterials.Length > 1);
        if (r0 == null) return;
        var hairMat = r0.sharedMaterials[1];
        var assetDir = "Assets/VGAC/" + name;
        ResetToBindPose(go);
        var b = r0.bounds;
        float h = b.size.y;
        var centre = new Vector3(b.center.x, b.max.y - 0.075f * h, b.center.z);
        var anim = go.GetComponent<Animator>();
        var headBone = anim != null && anim.isHuman ? anim.GetBoneTransform(HumanBodyBones.Head) : null;
        if (headBone != null) centre = headBone.position + Vector3.up * 0.045f * h;
        int size = 768;
        var rt = new RenderTexture(size, size, 24);
        var oldTarget = cam.targetTexture;
        var oldFov = cam.fieldOfView;
        cam.targetTexture = rt;
        cam.fieldOfView = 20f;
        var dist = 0.2f * h / (2f * Mathf.Tan(cam.fieldOfView * 0.5f * Mathf.Deg2Rad));
        // Unity's model faces +Z after import (forward -Z in the FBX, turned): the camera sits at the front-left
        var fwd = go.transform.forward;
        var view = (Quaternion.AngleAxis(-35f, Vector3.up) * fwd + Vector3.up * 0.15f).normalized;
        cam.transform.position = centre + view * dist;
        cam.transform.LookAt(centre);
        var suns = UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None).Where(l => l.type == LightType.Directional && l.enabled).ToList();
        var kept = suns.Select(l => l.transform.rotation).ToList();
        var lights = new[] { ("l1", (Quaternion.AngleAxis(-40f, Vector3.up) * fwd + Vector3.up * 1.0f).normalized),
                             ("l2", (Quaternion.AngleAxis(100f, Vector3.up) * fwd + Vector3.up * 0.6f).normalized) };
        var lit = BuildMaterialFrom(assetDir, "hair_base_color.png", "hair_normal.png", "hair_ao.png",
                                    "T_" + name + "_Hair_MetallicSmoothness.png", new Report());
        var mats = r0.sharedMaterials;
        foreach (var (tag, mat) in new[] { (hairMat.shader.name == HairShaderName ? "vgac" : "own", hairMat), ("lit", lit) })
        {
            mats[1] = mat;
            r0.sharedMaterials = mats;
            foreach (var (ltag, dir) in lights)
            {
                foreach (var s in suns) s.transform.rotation = Quaternion.LookRotation(-dir);
                cam.Render();
                RenderTexture.active = rt;
                var tex = new Texture2D(size, size, TextureFormat.RGB24, false);
                tex.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                tex.Apply();
                RenderTexture.active = null;
                var file = Path.Combine(outDir, "hair_" + tag + "_" + ltag + ".png");
                File.WriteAllBytes(file, tex.EncodeToPNG());
                UnityEngine.Object.DestroyImmediate(tex);
                log.Add(file);
            }
        }
        mats[1] = hairMat;
        r0.sharedMaterials = mats;
        for (int i = 0; i < suns.Count; i++) suns[i].transform.rotation = kept[i];
        cam.targetTexture = oldTarget;
        cam.fieldOfView = oldFov;
        UnityEngine.Object.DestroyImmediate(lit);
        Debug.Log("VGAC hair shots: " + hairMat.shader.name + " and " + PickShader().name + ", suns " + suns.Count);
    }

    [Serializable] class SnapReport { public bool ok; public string error = ""; public List<string> files = new List<string>(); public List<string> loopProbes = new List<string>(); public string showcase = ""; public List<string> warnings = new List<string>(); }
}

// Runs inside an open Editor: after any import that brings in a hand-off request, and once after each script load
// (which also covers a newer VgacImport.cs dropped into the open project). The work is deferred to delayCall, outside
// the import, because importing assets from inside OnPostprocessAllAssets is not allowed. Batch mode is left to
// VgacImport.Run and VgacImport.ImportPending. Nothing here throws, and nothing runs in a project with no requests.
class VgacImportWatcher : AssetPostprocessor
{
    static bool scheduled;

    static void OnPostprocessAllAssets(string[] importedAssets, string[] deletedAssets, string[] movedAssets, string[] movedFromAssetPaths)
    {
        try
        {
            if (Application.isBatchMode || importedAssets == null) return;
            foreach (var p in importedAssets)
                if (p != null && p.EndsWith("/" + VgacImport.RequestFile, StringComparison.Ordinal) && p.StartsWith("Assets/VGAC/", StringComparison.Ordinal))
                {
                    Schedule();
                    return;
                }
        }
        catch (Exception e) { Debug.LogError("VGAC: watcher failed: " + e); }
    }

    [InitializeOnLoadMethod]
    static void AtLoad()
    {
        try
        {
            if (Application.isBatchMode) return;
            // Version 16: also once after a newer importer arrives, to bring older characters' move facing up to date.
            Schedule();
        }
        catch (Exception e) { Debug.LogError("VGAC: watcher failed at load: " + e); }
    }

    static void Schedule()
    {
        if (scheduled) return;
        scheduled = true;
        EditorApplication.delayCall += () =>
        {
            scheduled = false;
            try
            {
                if (EditorApplication.isCompiling || EditorApplication.isUpdating || EditorApplication.isPlayingOrWillChangePlaymode) { Schedule(); return; }
                VgacImport.RefreshClipFacing();  // nothing to do (and quick) when every clips.fbx carries the mark
                VgacImport.Sweep();
            }
            catch (Exception e) { Debug.LogError("VGAC: pending import failed: " + e); }
        };
    }
}
