import Link from "next/link";
import { SnapshotStatus } from "@/components/snapshot-status";

const workflows = [
  {
    kicker: "FEATURE 01",
    title: "Prioritization & localization",
    text: "Inspect forecast priority grids alongside traceable NASA FIRMS satellite detections.",
    href: "/prioritization",
    cta: "Open monitoring board",
  },
  {
    kicker: "FEATURE 02",
    title: "Response planning",
    text: "Create an auditable personnel and ground-response vehicle planning reference for a field scenario.",
    href: "/response-planner",
    cta: "Open planner",
  },
];

export default function OverviewPage() {
  return (
    <main className="page stack-xl">
      <section className="hero-grid">
        <div className="hero-copy stack-md">
          <p className="eyebrow">KALIMANTAN FIREWATCH</p>
          <h1>Find what needs attention.<br /><span>Plan what can be verified.</span></h1>
          <p className="lede">
            Decision support for peatland and land-fire monitoring in Kalimantan.
            It surfaces evidence and planning references—it does not dispatch resources.
          </p>
          <div className="hero-actions">
            <Link className="button button-primary" href="/prioritization">Open monitoring board <span>→</span></Link>
            <Link className="button button-quiet" href="/evidence">Review evidence</Link>
          </div>
        </div>
        <SnapshotStatus />
      </section>

      <section className="workflow-grid" aria-label="Dashboard workflows">
        {workflows.map((workflow) => (
          <article className="workflow-card" key={workflow.href}>
            <p className="eyebrow">{workflow.kicker}</p>
            <h2>{workflow.title}</h2>
            <p>{workflow.text}</p>
            <Link href={workflow.href} className="text-link">{workflow.cta} <span>→</span></Link>
          </article>
        ))}
      </section>

      <section className="guardrail-card">
        <div><p className="eyebrow">OPERATING BOUNDARY</p><h2>Built for an operator in the loop.</h2></div>
        <p>Forecast grids are monitoring signals. FIRMS entries are satellite detections, not confirmed independent incidents. Every response plan requires field verification of perimeter, water, access, roster, and fleet availability.</p>
      </section>
    </main>
  );
}
