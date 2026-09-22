import { useState } from "react";
import { getAssistantAnswer } from "../api/client";

const SUGGESTED_QUESTIONS = [
  "Why did this route's index move recently?",
  "How is the Airfare Price Index calculated?",
  "Is this a good time to book?",
];

export default function AssistantPanel({ selectedRoute }) {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function ask(q) {
    const text = q.trim();
    if (!text || loading) return;
    setLoading(true);
    setError(null);
    try {
      const answer = await getAssistantAnswer({ route: selectedRoute ?? undefined, question: text });
      setResult(answer);
    } catch (err) {
      setError("Something went wrong asking the assistant. Please try again.");
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    ask(question);
  }

  return (
    <section className="glass p-5 md:p-6">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-white font-semibold text-lg">Ask about this index</h2>
        {selectedRoute && (
          <span className="text-xs text-white/45 bg-white/5 border border-white/10 rounded-full px-2.5 py-1">
            grounded in {selectedRoute}
          </span>
        )}
      </div>
      <p className="text-white/45 text-sm mb-4">
        Get a plain-English explanation, grounded in the actual index data and methodology — not a guess.
      </p>

      <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-2 mb-3">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={
            selectedRoute
              ? `Ask about ${selectedRoute}'s price index…`
              : "Ask about the Airfare Price Index…"
          }
          className="flex-1 bg-white/10 border border-white/15 text-white text-sm rounded-lg px-3 py-2.5 placeholder:text-white/35 focus:outline-none focus:ring-1 focus:ring-white/40"
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="px-4 py-2.5 rounded-lg text-sm font-medium bg-white hover:bg-neutral-200 disabled:opacity-30 disabled:cursor-not-allowed text-black transition-colors shrink-0"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      <div className="flex flex-wrap gap-2 mb-4">
        {SUGGESTED_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => {
              setQuestion(q);
              ask(q);
            }}
            disabled={loading}
            className="text-xs text-white/60 bg-white/5 hover:bg-white/10 border border-white/10 rounded-full px-3 py-1.5 transition-colors disabled:opacity-40"
          >
            {q}
          </button>
        ))}
      </div>

      {loading && <div className="h-24 rounded-xl bg-white/5 animate-pulse" />}

      {error && !loading && (
        <div className="glass border border-white/30 rounded-xl p-4 text-sm text-white">
          {error}
        </div>
      )}

      {result && !loading && !error && (
        <div className="glass border border-white/10 rounded-xl p-4">
          <p className="text-sm text-white/90 leading-relaxed">{result.answer}</p>
          {result.sources?.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t border-white/10">
              {result.sources.map((s) => (
                <span
                  key={s.label}
                  title={s.detail}
                  className="text-xs text-white/50 bg-white/5 border border-white/10 rounded-full px-2.5 py-1"
                >
                  {s.label}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
