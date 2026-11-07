// Joint facts shared by the 3D viewer and the joint editor's controls (kept apart from the viewer
// so the editor's page doesn't load three.js just for them).

/** Joints whose facing means something (a knee cap, an elbow, the chest): not collarbones, which
 *  run sideways, nor the pelvis, toes, sockets or root. */
export const HAS_FACING =
  /^(spine_\d+|neck_\d+|Head|upperarm_[lr]|lowerarm_[lr]|hand_[lr]|thigh_[lr]|calf_[lr]|foot_[lr])$/;
