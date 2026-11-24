// Thumbnail generator - image model retired in the Oct 23, 2026 shutdown.
import OpenAI from "openai";

const client = new OpenAI();
const IMAGE_MODEL = "gpt-image-1";

export async function thumbnail(prompt: string) {
  return client.images.generate({ model: IMAGE_MODEL, prompt });
}
