// Build the normal application without reading any frontend .env files or
// exposing inherited VITE_* values to the synthetic browser test bundle.
import { build } from 'vite';

await build({ envDir: false, envPrefix: [] });
