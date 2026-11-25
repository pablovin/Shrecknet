# Unreleased — Docker build cache

- Keep pip's download cache in the BuildKit cache mount during Shrecknet image
  builds instead of writing it into the application user's final image layer.
  This reduces image size and avoids repeated cache data during release exports.
