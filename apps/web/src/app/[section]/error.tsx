"use client";
export default function OperationalReadError({retry}:{retry:()=>void}) {
  return <div className="page-content"><h1>Operational read unavailable</h1><p role="alert">These observations could not be displayed. No outcomes are inferred.</p><button type="button" className="health-link" onClick={()=>retry()}>Read observations again</button><p>Reading again does not repeat a command or change safety controls.</p></div>;
}
