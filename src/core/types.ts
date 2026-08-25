export interface Speaker {
  name: string;
}

export interface TranscriptSegment {
  id: string;
  speaker: string;
  startSeconds: number;
  endSeconds: number;
  text: string;
}

export interface Chapter {
  id: string;
  startSeconds: number;
  endSeconds: number;
  title: string;
  summary: string;
}

export interface ArtifactSource {
  url?: string;
  urls?: string[];
  data?: string;
}

export interface Artifact {
  id: string;
  type: string;
  startSeconds: number;
  endSeconds: number;
  title: string;
  source?: ArtifactSource;
  payload?: unknown;
}

export interface EpisodeManifest {
  schemaVersion: 1;
  speakers: Record<string, Speaker>;
  transcript: TranscriptSegment[];
  chapters: Chapter[];
  artifacts: Artifact[];
}

export interface RendererContext {
  artifact: Artifact;
  container: HTMLElement;
  media: HTMLMediaElement;
}

export interface RendererInstance {
  sync(currentTime: number, playing: boolean): void;
  destroy(): void;
}

export type ArtifactRenderer = (
  context: RendererContext,
) => RendererInstance | Promise<RendererInstance>;

export interface MountOptions {
  media: string | HTMLMediaElement;
  container?: string | HTMLElement;
  after?: string | HTMLElement;
  manifest?: EpisodeManifest;
  manifestUrl?: string;
}

export interface InitOptions {
  audio: string | HTMLMediaElement;
  manifest: string | EpisodeManifest;
  after?: string | HTMLElement;
}

export interface PodcastArtifactsInstance {
  destroy(): void;
}
