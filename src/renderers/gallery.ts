import type { ArtifactRenderer, RendererInstance } from "../core/types";

export const galleryRenderer: ArtifactRenderer = ({ artifact, container }) => {
  const urls = artifact.source?.urls;
  const firstUrl = urls?.[0];
  if (!urls || !firstUrl) {
    throw new Error(`Артефакт ${artifact.id} не содержит изображений`);
  }

  const gallery = document.createElement("figure");
  gallery.className = "pa-gallery";
  const image = document.createElement("img");
  image.src = firstUrl;
  image.alt = artifact.title;
  gallery.append(image);

  let index = 0;
  if (urls.length > 1) {
    const controls = document.createElement("figcaption");
    const previous = document.createElement("button");
    const counter = document.createElement("span");
    const next = document.createElement("button");
    previous.type = next.type = "button";
    previous.textContent = "←";
    next.textContent = "→";
    previous.setAttribute("aria-label", "Предыдущее изображение");
    next.setAttribute("aria-label", "Следующее изображение");

    const show = (nextIndex: number): void => {
      index = (nextIndex + urls.length) % urls.length;
      image.src = urls[index] ?? firstUrl;
      image.alt = `${artifact.title}, изображение ${index + 1} из ${urls.length}`;
      counter.textContent = `${index + 1} / ${urls.length}`;
    };
    previous.addEventListener("click", () => show(index - 1));
    next.addEventListener("click", () => show(index + 1));
    show(0);
    controls.append(previous, counter, next);
    gallery.append(controls);
  }

  container.append(gallery);
  const instance: RendererInstance = {
    sync() {},
    destroy() {
      gallery.remove();
    },
  };
  return instance;
};
