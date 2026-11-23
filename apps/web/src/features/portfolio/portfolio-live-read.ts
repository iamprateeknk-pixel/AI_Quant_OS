export type Position={instrument_id:string;base_currency:string;quote_currency:string;mark_price:string|null;net_quantity:string;cash_flow_quote:string;marked_pnl_quote:string|null};
export type Currency={currency:string;filled_balance:string;minimum_balance:string|null;maximum_balance:string|null;worst_case_exposure_reporting:string|null;issue:string|null};
export type CurrencyPortfolio={reporting_currency:string;valuation_available:boolean;marked_pnl_reporting:string|null;gross_worst_case_exposure_reporting:string|null;currencies:Currency[]};
export type PortfolioRead<T>={ok:true;value:T}|{ok:false;reason:string};
export type PortfolioReads=[PortfolioRead<{positions:Position[]}>,PortfolioRead<CurrencyPortfolio>];
export type PortfolioSnapshot={observed_at:string;positions:PortfolioRead<{positions:Position[]}>;currencyPortfolio:PortfolioRead<CurrencyPortfolio>};
const MAX_BYTES=32768;
const record=(value:unknown):value is Record<string,unknown>=>typeof value==="object"&&value!==null&&!Array.isArray(value);
const exact=(value:Record<string,unknown>,keys:string[])=>Object.keys(value).length===keys.length&&keys.every(key=>Object.prototype.hasOwnProperty.call(value,key));
function read<T>(value:unknown,parse:(value:unknown)=>T|null):PortfolioRead<T>|null{
 if(!record(value))return null;
 if(value.ok===false&&exact(value,["ok","reason"])&&typeof value.reason==="string"&&value.reason.length<=80)return{ok:false,reason:value.reason};
 if(value.ok===true&&exact(value,["ok","value"])) {const parsed=parse(value.value);return parsed===null?null:{ok:true,value:parsed};}
 return null;
}
function positions(value:unknown):{positions:Position[]}|null{
 if(!record(value)||!exact(value,["positions"])||!Array.isArray(value.positions)||value.positions.length>128)return null;
 const rows:Position[]=[];
 for(const item of value.positions){if(!record(item)||!exact(item,["instrument_id","base_currency","quote_currency","mark_price","net_quantity","cash_flow_quote","marked_pnl_quote"])||typeof item.instrument_id!=="string"||typeof item.base_currency!=="string"||typeof item.quote_currency!=="string"||(item.mark_price!==null&&typeof item.mark_price!=="string")||typeof item.net_quantity!=="string"||typeof item.cash_flow_quote!=="string"||(item.marked_pnl_quote!==null&&typeof item.marked_pnl_quote!=="string"))return null;rows.push(item as Position);}
 return{positions:rows};
}
function currencyPortfolio(value:unknown):CurrencyPortfolio|null{
 if(!record(value)||!exact(value,["reporting_currency","valuation_available","marked_pnl_reporting","gross_worst_case_exposure_reporting","currencies"])||typeof value.reporting_currency!=="string"||typeof value.valuation_available!=="boolean"||(value.marked_pnl_reporting!==null&&typeof value.marked_pnl_reporting!=="string")||(value.gross_worst_case_exposure_reporting!==null&&typeof value.gross_worst_case_exposure_reporting!=="string")||!Array.isArray(value.currencies)||value.currencies.length>64)return null;
 const rows:Currency[]=[];
 for(const item of value.currencies){if(!record(item)||!exact(item,["currency","filled_balance","minimum_balance","maximum_balance","worst_case_exposure_reporting","issue"])||typeof item.currency!=="string"||typeof item.filled_balance!=="string"||(item.minimum_balance!==null&&typeof item.minimum_balance!=="string")||(item.maximum_balance!==null&&typeof item.maximum_balance!=="string")||(item.worst_case_exposure_reporting!==null&&typeof item.worst_case_exposure_reporting!=="string")||(item.issue!==null&&typeof item.issue!=="string"))return null;rows.push(item as Currency);}
 return{reporting_currency:value.reporting_currency,valuation_available:value.valuation_available,marked_pnl_reporting:value.marked_pnl_reporting,gross_worst_case_exposure_reporting:value.gross_worst_case_exposure_reporting,currencies:rows};
}
export function parsePortfolioSnapshot(value:unknown):PortfolioSnapshot|null{
 if(!record(value)||!exact(value,["observed_at","positions","currencyPortfolio"])||typeof value.observed_at!=="string")return null;
 const time=Date.parse(value.observed_at);
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.observed_at)||!Number.isFinite(time)||new Date(time).toISOString()!==value.observed_at)return null;
 const positionRead=read(value.positions,positions),currencyRead=read(value.currencyPortfolio,currencyPortfolio);
 return positionRead&&currencyRead?{observed_at:value.observed_at,positions:positionRead,currencyPortfolio:currencyRead}:null;
}
export async function readLivePortfolio(signal:AbortSignal):Promise<PortfolioSnapshot|null>{
 const response=await fetch("/api/portfolio/observations",{cache:"no-store",redirect:"error",credentials:"same-origin",signal});
 if(!response.ok){await response.body?.cancel();return null;}
 const reader=response.body?.getReader();if(!reader)return null;
 const chunks:Uint8Array[]=[];let length=0;
 try{for(;;){const{done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>MAX_BYTES)throw new Error("Portfolio read exceeded capacity");chunks.push(value);}}
 finally{await reader.cancel();}
 const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
 return parsePortfolioSnapshot(JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes)));
}
