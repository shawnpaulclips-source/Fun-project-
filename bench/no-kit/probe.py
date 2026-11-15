import bpy, addon_utils
addon_utils.enable('cycles')
sc = bpy.context.scene
print("engine enum:", [i.identifier for i in sc.render.bl_rna.properties['engine'].enum_items])
sc.render.engine='CYCLES'
print("ok engine", sc.render.engine)
c=sc.cycles
for k in ['samples','use_denoising','denoiser','device','use_adaptive_sampling','adaptive_threshold','max_bounces','use_fast_gi','tile_size','use_auto_tile','preview_samples','use_light_tree','caustics_reflective','blur_glossy','sample_clamp_indirect','transparent_max_bounces','transmission_bounces','volume_bounces','diffuse_bounces','glossy_bounces']:
    print("  cycles."+k, "=", getattr(c,k,"MISSING"))
print("denoiser enum:", [i.identifier for i in c.bl_rna.properties['denoiser'].enum_items])
print("view_transform:", [i.identifier for i in sc.view_settings.bl_rna.properties['view_transform'].enum_items])
print("colorspaces:", [i.identifier for i in bpy.types.ColorManagedInputColorspaceSettings.bl_rna.properties['name'].enum_items][:20])
