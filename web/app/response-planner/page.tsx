import { Suspense } from "react";
import { ResponsePlanner } from "@/components/response-planner";

export default function ResponsePlannerPage() {
  return <Suspense fallback={<main className="page"><p className="muted">Loading response planner…</p></main>}><ResponsePlanner /></Suspense>;
}
