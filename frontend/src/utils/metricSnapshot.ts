/** Each row is a cumulative snapshot, not an increment. IDs break equal-time ties. */
export function latestMetricSnapshot<T extends {id:number;collected_at:string}>(items:readonly T[]):T|null {
  const timestamp=(item:T)=>{const value=Date.parse(item.collected_at);return Number.isFinite(value)?value:-Infinity;};
  return items.reduce<T|null>((latest,item)=>!latest||timestamp(item)>timestamp(latest)||(timestamp(item)===timestamp(latest)&&item.id>latest.id)?item:latest,null);
}
