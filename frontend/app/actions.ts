"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { apiFetch } from "./lib/api";

// Create a study, then bounce the founder to its detail page.
export async function createStudy(formData: FormData) {
  const title = String(formData.get("title") ?? "").trim();
  const goal = String(formData.get("goal") ?? "").trim();
  const seedQuestions = String(formData.get("seedQuestions") ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const res = await apiFetch("/studies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, goal, seedQuestions }),
  });

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Failed to create study (${res.status}): ${detail}`);
  }

  const { studyId } = await res.json();
  revalidatePath("/");
  // redirect() throws internally, so it must sit outside any try/catch.
  redirect(`/studies/${studyId}`);
}

// Flip a study draft -> live (PATCH /studies/{id}).
export async function publishStudy(formData: FormData) {
  const id = String(formData.get("id") ?? "");

  const res = await apiFetch(`/studies/${id}`, { method: "PATCH" });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Failed to publish study (${res.status}): ${detail}`);
  }

  revalidatePath(`/studies/${id}`);
  revalidatePath("/");
}
