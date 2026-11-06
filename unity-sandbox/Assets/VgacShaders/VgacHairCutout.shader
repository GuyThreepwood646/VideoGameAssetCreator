// VgacHairCutout Version 1
// Cut-out hair shader for Video Game Asset Creator characters, built-in render pipeline (a surface shader).
// For hair from the library (docs/HAIR-LIBRARY-PLAN.md): hair cards (flat strips with a see-through texture) and shells
// marked two-sided. The importer (Assets/Editor/VgacImport.cs, Version 17 and later) puts it on the hair's own material
// when the export passes -vgacHairCutout on or -vgacHairTwoSided on; the backend copies this file into a user's project
// next to the importer when the copy there is missing or has a lower Version. VGAC/Hair (VgacHair.shader) is unchanged
// and still used for opaque one-sided hair.
//
// The same lighting as VGAC/Hair (Standard lighting with the hair's colour, normal map and occlusion, plus the two-band
// Kajiya-Kay strand highlight from the flow map), with:
// - alpha cutout: texels whose alpha is under _Cutoff are not drawn (alphatest), in the AlphaTest queue;
// - culling from _Cull (0 off for two-sided hair, 2 back faces culled); on a back face the normal is turned round
//   (VFACE), so the inside of a card is lit as its own front;
// - shadows cast with the same cutout (addshadow).
// URP and HDRP are not covered by this file.
Shader "VGAC/HairCutout"
{
    Properties
    {
        _MainTex ("Hair colour", 2D) = "white" {}
        [Normal] _BumpMap ("Normal map", 2D) = "bump" {}
        _OcclusionMap ("Occlusion", 2D) = "white" {}
        _OcclusionStrength ("Occlusion strength", Range(0, 1)) = 1
        _MetallicGlossMap ("Metal (R) and smoothness (A)", 2D) = "black" {}
        _FlowMap ("Flow map (strand direction)", 2D) = "gray" {}
        _SpecColor1 ("Primary highlight colour", Color) = (1, 1, 1, 1)
        _SpecStrength1 ("Primary highlight strength", Range(0, 2)) = 0.25
        _SpecExp1 ("Primary highlight sharpness", Range(4, 512)) = 80
        _Shift1 ("Primary shift (toward the tips)", Range(-1, 1)) = 0.12
        _SpecStrength2 ("Secondary highlight strength", Range(0, 2)) = 0.3
        _SpecExp2 ("Secondary highlight sharpness", Range(4, 256)) = 16
        _IsoKeep ("Round highlight kept (Standard smoothness factor)", Range(0, 1)) = 0.35
        _Shift2 ("Secondary shift (toward the roots)", Range(-1, 1)) = -0.12
        _ShiftNoise ("Shift noise", Range(0, 1)) = 0.35
        _FlowSign ("Flow v sign", Float) = 1
        _Cutoff ("Alpha cutoff", Range(0, 1)) = 0.5
        [Enum(UnityEngine.Rendering.CullMode)] _Cull ("Cull (0 two-sided)", Float) = 0
    }
    SubShader
    {
        Tags { "RenderType" = "TransparentCutout" "Queue" = "AlphaTest" "IgnoreProjector" = "True" }
        LOD 300
        Cull [_Cull]

        CGPROGRAM
        #pragma surface surf Hair fullforwardshadows addshadow alphatest:_Cutoff
        #pragma target 3.0
        #include "UnityPBSLighting.cginc"

        sampler2D _MainTex;
        sampler2D _BumpMap;
        sampler2D _OcclusionMap;
        sampler2D _MetallicGlossMap;
        sampler2D _FlowMap;
        half _OcclusionStrength;
        fixed4 _SpecColor1;
        half _SpecStrength1, _SpecExp1, _Shift1, _SpecStrength2, _SpecExp2, _Shift2, _ShiftNoise, _FlowSign, _IsoKeep;

        struct Input
        {
            float2 uv_MainTex;
            float3 worldNormal;
            fixed facing : VFACE;
            INTERNAL_DATA
        };

        struct SurfaceOutputHair
        {
            fixed3 Albedo;
            fixed3 Normal;
            fixed3 Emission;
            half Specular;
            fixed Gloss;
            fixed Alpha;
            half Occlusion;
            float3 Strand;   // world-space strand direction
            half Shift;      // per-texel shift noise, -1..1
            half Smooth;     // smoothness from the metal-and-shine map
        };

        // Kajiya-Kay: the highlight of a thread along T for the half vector H is sin(T, H) raised to a power.
        half StrandSpec(float3 T, float3 N, float shift, float3 H, half expo)
        {
            float3 t = normalize(T + shift * N);
            float dotTH = dot(t, H);
            float sinTH = sqrt(saturate(1.0 - dotTH * dotTH));
            float dirAtten = smoothstep(-1.0, 0.0, dotTH);
            return dirAtten * pow(sinTH, expo);
        }

        inline SurfaceOutputStandard AsStandard(SurfaceOutputHair s)
        {
            SurfaceOutputStandard o;
            o.Albedo = s.Albedo;
            o.Normal = s.Normal;
            o.Emission = s.Emission;
            o.Metallic = 0;
            o.Smoothness = s.Smooth * _IsoKeep;
            o.Occlusion = s.Occlusion;
            o.Alpha = s.Alpha;
            return o;
        }

        inline half4 LightingHair(SurfaceOutputHair s, half3 viewDir, UnityGI gi)
        {
            half4 c = LightingStandard(AsStandard(s), viewDir, gi);
            float3 N = normalize(s.Normal);
            float3 L = gi.light.dir;
            float3 H = normalize(L + viewDir);
            half nl = saturate(dot(N, L));
            float noise = s.Shift * _ShiftNoise;
            half spec1 = StrandSpec(s.Strand, N, _Shift1 + noise, H, _SpecExp1) * _SpecStrength1;
            half spec2 = StrandSpec(s.Strand, N, _Shift2 + noise, H, _SpecExp2) * _SpecStrength2;
            // the strands shadow each other: the highlight fades where the light grazes the surface
            half shadowing = saturate(nl * 1.5 + 0.1);
            half3 spec = (spec1 * _SpecColor1.rgb + spec2 * s.Albedo) * gi.light.color * shadowing * lerp(0.6, 1.0, s.Smooth) * s.Occlusion;
            c.rgb += spec;
            return c;
        }

        inline void LightingHair_GI(SurfaceOutputHair s, UnityGIInput data, inout UnityGI gi)
        {
            LightingStandard_GI(AsStandard(s), data, gi);
        }

        void surf(Input IN, inout SurfaceOutputHair o)
        {
            fixed4 c = tex2D(_MainTex, IN.uv_MainTex);
            o.Albedo = c.rgb;
            o.Alpha = c.a;
            o.Normal = UnpackNormal(tex2D(_BumpMap, IN.uv_MainTex));
            // a back face (two-sided cards) is lit as its own front: turn the normal round
            if (IN.facing < 0) o.Normal.z = -o.Normal.z;
            half ao = tex2D(_OcclusionMap, IN.uv_MainTex).g;
            o.Occlusion = lerp(1.0, ao, _OcclusionStrength);
            o.Smooth = tex2D(_MetallicGlossMap, IN.uv_MainTex).a;
            half4 f = tex2D(_FlowMap, IN.uv_MainTex);
            float2 d = f.rg * 2.0 - 1.0;
            d.y *= _FlowSign;
            // the flow is in the texture's own directions: WorldNormalVector carries a tangent-space vector to world space
            o.Strand = normalize(WorldNormalVector(IN, float3(d.x, d.y, 0.0)) + 1e-5);
            o.Shift = f.a * 2.0 - 1.0;
        }
        ENDCG
    }
    FallBack "Legacy Shaders/Transparent/Cutout/Diffuse"
}
