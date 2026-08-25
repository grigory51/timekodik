export interface ManifestatorDebugStage {
  schemaVersion: 1;
  episodeId: string;
  stage: string;
  label: string;
  status: "complete" | "pending";
  generatedAt?: string;
  data?: Record<string, unknown>;
}

export interface ManifestatorDebugReport {
  schemaVersion: 1;
  episodeId: string;
  stages: ManifestatorDebugStage[];
}

export interface ManifestatorDebugInstance {
  open(): void;
  destroy(): void;
}
