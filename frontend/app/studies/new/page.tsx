import Link from "next/link";
import { createStudy } from "../../actions";

export default function NewStudyPage() {
  return (
    <>
      <h1>New study</h1>
      <p className="subtitle">
        The interviewer agent uses your goal and seed questions to run an adaptive voice
        interview — it asks follow-ups, not a fixed script.
      </p>

      <form action={createStudy}>
        <label htmlFor="title">Title</label>
        <input id="title" name="title" type="text" required placeholder="Onboarding friction study" />

        <label htmlFor="goal">
          Research goal <span className="hint">— what you&apos;re trying to learn</span>
        </label>
        <textarea
          id="goal"
          name="goal"
          placeholder="Find where new users stall in their first session."
        />

        <label htmlFor="seedQuestions">
          Seed questions <span className="hint">— one per line</span>
        </label>
        <textarea
          id="seedQuestions"
          name="seedQuestions"
          rows={5}
          placeholder={"Walk me through your first day using the product.\nWhat was the first thing that confused you?"}
        />

        <div className="row-between mt-6">
          <Link href="/" className="btn secondary">
            Cancel
          </Link>
          <button type="submit" className="btn">
            Create study
          </button>
        </div>
      </form>
    </>
  );
}
