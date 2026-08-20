import { defineConfig } from 'astro/config';

// Static output: Astro builds plain HTML into dist/.
// The dynamic part of the site is the Cloudflare Pages Function
// in web/functions/, which Cloudflare deploys alongside the static files.
export default defineConfig({
  output: 'static',
});
