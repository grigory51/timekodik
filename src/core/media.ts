export function seekAndPlay(media: HTMLMediaElement, seconds: number): void {
  media.currentTime = seconds;
  void media.play().catch((error: unknown) => {
    console.error("Аудио не запустилось после перемотки", error);
  });
}
