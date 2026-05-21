import { Container, getRandom } from "@cloudflare/containers";

export class AudioAnalyzerContainer extends Container {
  defaultPort = 8000;
  requiredPorts = [8000];
  sleepAfter = "30m";
  enableInternet = true;
  pingEndpoint = "localhost:8000/health";
  entrypoint = [
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--workers",
    "3",
  ];

  override onStart(): void {
    console.log("[AudioAnalyzerContainer] started");
  }

  override onStop(params: { exitCode?: number; reason?: string }): void {
    console.log("[AudioAnalyzerContainer] stopped", params);
  }

  override onError(error: unknown): void {
    console.error("[AudioAnalyzerContainer] error", error);
    throw error;
  }
}

interface Env {
  AUDIO_ANALYZER_CONTAINER: DurableObjectNamespace<AudioAnalyzerContainer>;
  ANALYZER_POOL_SIZE?: string;

  ANALYZER_SECRET: string;
  LARAVEL_API_URL: string;
  LARAVEL_API_SECRET: string;
  R2_ENDPOINT: string;
  R2_ACCESS_KEY_ID: string;
  R2_SECRET_ACCESS_KEY: string;
  R2_BUCKET: string;
  R2_REGION?: string;

  APP_ENV?: string;
  MAX_FILE_SIZE_MB?: string;
  MAX_DURATION_SECONDS?: string;
  BIRDNET_CONFIDENCE_THRESHOLD?: string;
  BIRDNET_PUBLISH_CONFIDENCE_THRESHOLD?: string;
  BIRDNET_REPEATED_CONFIDENCE_THRESHOLD?: string;
  BIRDNET_MAX_DETECTIONS?: string;
  BIRDNET_MODEL_PATH?: string;
  LOG_LEVEL?: string;
}

function containerEnv(env: Env): Record<string, string> {
  return {
    ANALYZER_SECRET: env.ANALYZER_SECRET,
    LARAVEL_API_URL: env.LARAVEL_API_URL,
    LARAVEL_API_SECRET: env.LARAVEL_API_SECRET,
    R2_ENDPOINT: env.R2_ENDPOINT,
    R2_ACCESS_KEY_ID: env.R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY: env.R2_SECRET_ACCESS_KEY,
    R2_BUCKET: env.R2_BUCKET,
    R2_REGION: env.R2_REGION ?? "auto",
    APP_ENV: env.APP_ENV ?? "production",
    MAX_FILE_SIZE_MB: env.MAX_FILE_SIZE_MB ?? "500",
    MAX_DURATION_SECONDS: env.MAX_DURATION_SECONDS ?? "600",
    BIRDNET_CONFIDENCE_THRESHOLD: env.BIRDNET_CONFIDENCE_THRESHOLD ?? "0.25",
    BIRDNET_PUBLISH_CONFIDENCE_THRESHOLD: env.BIRDNET_PUBLISH_CONFIDENCE_THRESHOLD ?? "0.45",
    BIRDNET_REPEATED_CONFIDENCE_THRESHOLD: env.BIRDNET_REPEATED_CONFIDENCE_THRESHOLD ?? "0.32",
    BIRDNET_MAX_DETECTIONS: env.BIRDNET_MAX_DETECTIONS ?? "80",
    BIRDNET_MODEL_PATH: env.BIRDNET_MODEL_PATH ?? "/opt/birdnet/checkpoints/V2.4/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite",
    LOG_LEVEL: env.LOG_LEVEL ?? "INFO",
  };
}

function poolSize(env: Env): number {
  const parsed = Number.parseInt(env.ANALYZER_POOL_SIZE ?? "3", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 3;
}

function isAllowedPath(pathname: string): boolean {
  return pathname === "/health" || pathname === "/analyze";
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (!isAllowedPath(url.pathname)) {
      return new Response("Not found", { status: 404 });
    }

    const container = await getRandom(env.AUDIO_ANALYZER_CONTAINER, poolSize(env));

    await container.startAndWaitForPorts({
      ports: [8000],
      startOptions: {
        envVars: containerEnv(env),
      },
      cancellationOptions: {
        portReadyTimeoutMS: 60_000,
      },
    });

    return container.fetch(request);
  },
};
