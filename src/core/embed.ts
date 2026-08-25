export interface EmbedOptions {
  audio: string;
  manifest: string;
  after?: string;
}

export function readEmbedOptions(
  dataset: DOMStringMap,
): EmbedOptions | undefined {
  if (!dataset.audio && !dataset.manifest) {
    return undefined;
  }
  if (!dataset.audio || !dataset.manifest) {
    throw new Error("timekodik: data-audio и data-manifest обязательны");
  }
  return {
    audio: dataset.audio,
    manifest: dataset.manifest,
    ...(dataset.after ? { after: dataset.after } : {}),
  };
}
