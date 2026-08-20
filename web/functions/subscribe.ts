// Cloudflare Pages Function -> served at POST /subscribe
//
// Uses Neon's serverless driver, which talks to Postgres over plain HTTPS.
// That matters because Cloudflare Workers cannot open raw TCP sockets,
// so a normal Postgres client (like node-postgres) would not work here.

import { neon } from '@neondatabase/serverless';

interface Env {
  DATABASE_URL: string;
}

interface SubscribePayload {
  email?: string;
  trade?: string;
  state?: string;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.DATABASE_URL) {
    return json({ error: 'DATABASE_URL is not configured' }, 500);
  }

  let payload: SubscribePayload;
  const contentType = request.headers.get('content-type') ?? '';

  try {
    if (contentType.includes('application/json')) {
      payload = await request.json();
    } else {
      // Fall back to a plain HTML form submission.
      payload = Object.fromEntries(await request.formData()) as SubscribePayload;
    }
  } catch {
    return json({ error: 'Malformed request body' }, 400);
  }

  const email = payload.email?.trim().toLowerCase() ?? '';
  const trade = payload.trade?.trim() || null;
  const state = payload.state?.trim().toUpperCase() || null;

  // Deliberately loose: a real address check is a confirmation email, not a regex.
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return json({ error: 'A valid email address is required' }, 400);
  }

  try {
    const sql = neon(env.DATABASE_URL);
    // Re-subscribing updates the stored trade/state instead of erroring
    // on the UNIQUE constraint.
    await sql`
      INSERT INTO subscribers (email, trade, state)
      VALUES (${email}, ${trade}, ${state})
      ON CONFLICT (email) DO UPDATE
        SET trade = EXCLUDED.trade,
            state = EXCLUDED.state
    `;
    return json({ ok: true });
  } catch (error) {
    console.error('subscribe insert failed', error);
    return json({ error: 'Could not save subscription' }, 500);
  }
};
