export interface Env {
  /** URL unique (legacy) — utilisé si ANALYZER_URLS n'est pas défini */
  ANALYZER_URL: string;
  /** Liste d'URLs séparées par virgule pour multi-VPS workers */
  ANALYZER_URLS?: string;
  ANALYZER_SECRET: string;
  LARAVEL_API_URL?: string;
  LARAVEL_API_SECRET?: string;
}

interface R2EventBody {
  object?: {
    key?: string;
  };
}

const AUDIO_EXTENSIONS = new Set([
  "wav", "mp3", "flac", "ogg", "m4a", "aac", "wma", "webm",
]);

function isAudioFile(key: string): boolean {
  const ext = key.split(".").pop()?.toLowerCase();
  return ext !== undefined && AUDIO_EXTENSIONS.has(ext);
}

function extractSoundId(key: string): string | null {
  const parts = key.split("/");
  // Expected: sounds/original/{sound_id}/{filename}
  if (parts.length < 4) return null;
  const soundId = parts[2];
  if (!soundId || !/[\w-]+/.test(soundId)) return null;
  return soundId;
}

/** Parse les URLs des analyzers (supporte 1 ou plusieurs VPS workers) */
function getAnalyzerUrls(env: Env): string[] {
  if (env.ANALYZER_URLS) {
    return env.ANALYZER_URLS
      .split(",")
      .map((u) => u.trim())
      .filter((u) => u.length > 0);
  }
  return [env.ANALYZER_URL];
}

/** Mélange un tableau aléatoirement (Fisher-Yates) */
function shuffle<T>(array: T[]): T[] {
  const arr = [...array];
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

/**
 * Appelle l'endpoint /analyze sur un des workers disponibles.
 * Distribue la charge aléatoirement et failover automatique en cas de 5xx / timeout.
 */
async function callAnalyzer(
  urls: string[],
  soundId: string,
  objectKey: string,
  secret: string,
): Promise<{ ok: boolean; status: number; text: string }> {
  const shuffled = shuffle(urls);
  let lastResult: { ok: boolean; status: number; text: string } | null = null;

  for (const baseUrl of shuffled) {
    const url = `${baseUrl.replace(/\/$/, "")}/analyze`;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${secret}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          sound_id: soundId,
          original_r2_key: objectKey,
          force: false,
        }),
      });

      lastResult = {
        ok: res.ok,
        status: res.status,
        text: await res.text(),
      };

      if (res.ok) {
        return lastResult;
      }

      // Erreur client (4xx) : inutile de réessayer sur un autre worker
      if (res.status >= 400 && res.status < 500) {
        return lastResult;
      }

      // Erreur serveur (5xx) : on tente le worker suivant
      console.error(`[Worker] Analyzer ${baseUrl} returned ${res.status}: ${lastResult.text}`);
      continue;
    } catch (err) {
      console.error(`[Worker] Network error calling ${baseUrl}: ${err}`);
      continue;
    }
  }

  return lastResult ?? { ok: false, status: 0, text: "All analyzer workers unreachable" };
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (request.method === "GET" && new URL(request.url).pathname === "/health") {
      return new Response(JSON.stringify({ status: "ok" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response("Not found", { status: 404 });
  },

  async queue(batch: MessageBatch<R2EventBody>, env: Env, ctx: ExecutionContext): Promise<void> {
    const analyzerUrls = getAnalyzerUrls(env);

    if (analyzerUrls.length === 0) {
      console.error("[Worker] No analyzer URLs configured");
      for (const message of batch.messages) {
        message.retry();
      }
      return;
    }

    for (const message of batch.messages) {
      const body = message.body;
      const objectKey = body.object?.key;

      if (!objectKey || !objectKey.startsWith("sounds/original/")) {
        message.ack();
        continue;
      }

      if (!isAudioFile(objectKey)) {
        message.ack();
        continue;
      }

      const soundId = extractSoundId(objectKey);
      if (!soundId) {
        message.ack();
        continue;
      }

      const result = await callAnalyzer(analyzerUrls, soundId, objectKey, env.ANALYZER_SECRET);

      if (result.ok) {
        message.ack();
      } else {
        console.error(`[Worker] Analyzer error ${result.status}: ${result.text}`);
        message.retry();
      }
    }
  },
};
