import {acceptRealtimeFrame, parseRealtimeFrame, type RealtimeFrame} from "./frames";

/** Validate each bounded full-state event before exposing it to the browser. */
export function proxyRealtimeResponse(upstream: Response, signal: AbortSignal, clock = Date.now): Response {
  if(!upstream.ok || !upstream.headers.get("content-type")?.startsWith("text/event-stream") || !upstream.body) {
    void upstream.body?.cancel().catch(()=>{});
    throw new Error("Realtime upstream unavailable");
  }
  const reader=upstream.body.getReader(), decoder=new TextDecoder("utf-8",{fatal:true}), encoder=new TextEncoder();
  let buffer="", total=0, count=0, previous:RealtimeFrame|null=null, finished=false;
  const abort=()=>{void reader.cancel().catch(()=>{});};
  signal.addEventListener("abort",abort,{once:true});
  const release=async()=>{finished=true;signal.removeEventListener("abort",abort);await reader.cancel().catch(()=>{});};
  const stream=new ReadableStream<Uint8Array>({
    async pull(controller){
      try{
        while(!finished){
          if(signal.aborted) throw new Error("Realtime request cancelled");
          const split=buffer.indexOf("\n\n");
          if(split>=0){
            const event=buffer.slice(0,split);buffer=buffer.slice(split+2);
            if(encoder.encode(event).byteLength>33000 || ++count>20) throw new Error("Realtime capacity exceeded");
            const fields=new Map<string,string>();
            for(const line of event.split("\n")){
              if(line.startsWith(":") || line.startsWith("retry:")) continue;
              const index=line.indexOf(": "), key=line.slice(0,index);
              if(index<0 || !["id","event","data"].includes(key) || fields.has(key)) throw new Error("Invalid realtime event");
              fields.set(key,line.slice(index+2));
            }
            const reset=fields.get("event")==="reset";
            if(!reset && fields.get("event")!=="snapshot") throw new Error("Invalid realtime event type");
            const raw=JSON.parse(fields.get("data")??"null"), now=clock(), frame=parseRealtimeFrame(raw,now);
            if(!frame){
              const capturedAt=raw&&typeof raw==="object"&&typeof raw.captured_at==="string"?Date.parse(raw.captured_at):Number.NaN;
              const validUntil=raw&&typeof raw==="object"&&typeof raw.valid_until==="string"?Date.parse(raw.valid_until):Number.NaN;
              if(Number.isFinite(validUntil)&&now>=validUntil) throw new Error(`Realtime frame expired by ${Math.max(1,Math.ceil(now-validUntil))}ms`);
              if(Number.isFinite(capturedAt)&&capturedAt>now) throw new Error(`Realtime frame timestamp is in the future by ${Math.max(1,Math.ceil(capturedAt-now))}ms`);
              throw new Error("Realtime frame contract rejected");
            }
            if(fields.get("id")!==`${frame.epoch}:${frame.sequence}`) throw new Error("Realtime event ID mismatch");
            if(!acceptRealtimeFrame(previous,frame,reset)) throw new Error("Realtime event order rejected");
            previous=frame;
            const safe={schema_version:"1.0",mode:"PAPER",epoch:frame.epoch,sequence:frame.sequence,captured_at:frame.captured_at,valid_until:frame.valid_until,safety_state:frame.safety_state,order_count:frame.order_count,
              world:{schema_version:"1.0",source:"PAPER_SIMULATED",world_state_version:frame.world_version,as_of:frame.captured_at,valid_until:raw.world.valid_until,status:frame.world_status,instruments:frame.instruments,currency_twins:frame.currency_twins}};
            controller.enqueue(encoder.encode(`retry: 2000\nid: ${frame.epoch}:${frame.sequence}\nevent: ${reset?"reset":"snapshot"}\ndata: ${JSON.stringify(safe)}\n\n`));
            return;
          }
          if(encoder.encode(buffer).byteLength>33000) throw new Error("Realtime event exceeds capacity");
          const chunk=await reader.read();
          if(chunk.done){
            buffer+=decoder.decode();
            if(buffer.trim()) throw new Error("Truncated realtime event");
            await release();controller.close();return;
          }
          total+=chunk.value.byteLength;
          if(total>700000) throw new Error("Realtime stream exceeds capacity");
          buffer+=decoder.decode(chunk.value,{stream:true});
        }
      }catch(error){await release();controller.error(error);}
    },
    async cancel(){await release();},
  });
  return new Response(stream,{headers:{"Content-Type":"text/event-stream","Cache-Control":"no-store","X-Accel-Buffering":"no"}});
}
