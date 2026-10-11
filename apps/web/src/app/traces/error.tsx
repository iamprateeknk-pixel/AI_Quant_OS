"use client";

export default function TracesError({retry}:{retry:()=>void}) {
  return <div className="page-content"><h1>Trace read unavailable</h1><p role="alert">Saved trace observations could not be displayed. No outcomes are inferred.</p><button type="button" className="health-link" onClick={()=>retry()}>Read saved spans again</button><p>Reading again does not flush exports or change trading controls.</p></div>;
}
