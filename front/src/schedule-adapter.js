const PREFIX='/api/activity/v1/schedule';
export class ScheduleError extends Error {
  constructor(message,code,status=0,details={}){super(message);this.name='ScheduleError';Object.assign(this,{code,status,details})}
}
export function createScheduleAdapter({transport=(...args)=>fetch(...args),session=()=>null,authorize=async()=>{},onDenied=()=>{},onAuthorizationUnavailable=()=>{}}={}) {
  const controllers=new Set();let epoch=0;
  async function request(path,{method='GET',body,key,signal,binary=false}={}) {
    const generation=epoch,controller=new AbortController();controllers.add(controller);
    const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();
    let timedOut=false;const timer=setTimeout(()=>{timedOut=true;controller.abort()},15000);
    try {
      try{await authorize(controller.signal)}catch(error){
        if(generation!==epoch||signal?.aborted)throw new DOMException('Aborted','AbortError');
        if([401,403].includes(error.status))throw error;
        // This is specifically the permission probe, not the business transport.
        // Hide before propagating so callers cannot leave protected DOM visible.
        onAuthorizationUnavailable(error);
        throw new ScheduleError('暂不能核对排期访问权限，内容已隐藏','AUTHORIZATION_UNAVAILABLE');
      }
      if(generation!==epoch||controller.signal.aborted)throw new DOMException('Aborted','AbortError');
      const auth=session();if(!auth?.authenticated||!auth.permissions?.includes('accounts.manage'))throw new ScheduleError('当前会话无排期管理权限','FORBIDDEN',403);
      const response=await transport(PREFIX+path,{method,credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:{Accept:binary?'image/jpeg':'application/json',...(body!==undefined?{'Content-Type':'application/json','X-CSRF-Token':auth.csrfToken,'Idempotency-Key':key}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});
      if(binary&&response.ok){
        if(response.headers.get('Content-Type')?.split(';')[0]!=='image/jpeg')throw new ScheduleError('图片响应类型不匹配','INVALID_IMAGE');
        const bytes=await response.arrayBuffer();if(bytes.byteLength>512*1024)throw new ScheduleError('图片响应超过限制','IMAGE_LIMIT');
        if(generation!==epoch||controller.signal.aborted)throw new DOMException('Aborted','AbortError');return {bytes};
      }
      let envelope;try{envelope=await response.json()}catch{throw new ScheduleError('排期服务响应无效','INVALID_RESPONSE',response.status)}
      if(!response.ok||envelope.error)throw new ScheduleError(envelope.error?.message||'排期请求失败',envelope.error?.code||'HTTP_ERROR',response.status,envelope.error?.details);
      if(envelope.meta?.mode!=='hosted'||envelope.meta?.dataOrigin!=='activity_workbench'||envelope.meta?.complete!==true)throw new ScheduleError('排期来源或完整性未确认','SOURCE_MISMATCH');
      if(generation!==epoch||controller.signal.aborted)throw new DOMException('Aborted','AbortError');
      return envelope;
    }catch(error){
      if([401,403].includes(error.status))onDenied(error);
      if(timedOut)throw new ScheduleError('排期请求超时，操作结果需核对','TIMEOUT');throw error;
    }finally{clearTimeout(timer);controllers.delete(controller);signal?.removeEventListener('abort',abort)}
  }
  return {request,clear(){epoch++;for(const c of controllers)c.abort();controllers.clear()}};
}
export async function encodeImages(images) {
  if(images.length>6)throw new ScheduleError('每条评论最多6张图片','IMAGE_LIMIT',413);
  let total=0;const output=[];
  for(const {blob} of images){
    if(blob.type!=='image/jpeg'||blob.size>512*1024)throw new ScheduleError('单张压缩JPEG不能超过512KiB，请移除后重新选择','IMAGE_LIMIT',413);
    total+=blob.size;if(total>3*1024*1024)throw new ScheduleError('每条评论图片总量不能超过3MiB','IMAGE_LIMIT',413);
    const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
    output.push({mediaType:'image/jpeg',contentBase64:btoa(binary)});
  }
  return output;
}
