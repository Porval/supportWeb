import {ApiError,createQueryReader,createPagedReader} from './adapters.js';
import {queryFilters} from './dto.js';
const accountPrefix='/api/activity/v1/account';
const operationsPrefix='/api/activity/v1/operations';
export function createHostedAdapter(transport=(...args)=>fetch(...args)){
 let sessionValue=null;
 async function request(path,{account=false,signal,method='GET',body,commandKey,binary=false}={}){
  const controller=new AbortController();let timedOut=false;const cancel=()=>controller.abort();signal?.addEventListener('abort',cancel,{once:true});if(signal?.aborted)controller.abort();
  const isSlow=!account&&(path==='/materials'||path.includes('/sales-comparison')||path.startsWith('/products/sales-history')||path.includes('/shenjia-preview')||path==='/submissions/shenjia-batch'||/\/platforms\/[^/]+\/materials(?:\?|$)/.test(path)||/\/verify(?:\?|$)/.test(path));
  const timer=setTimeout(()=>{timedOut=true;controller.abort()},account&&['/login','/logout'].includes(path)?8000:isSlow?65000:15000);
  try{
   const response=await transport((account?accountPrefix:operationsPrefix)+path,{method,credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{Accept:binary?'application/octet-stream':'application/json',...(commandKey?{'Idempotency-Key':commandKey}:{}),...(body!==undefined?{'Content-Type':'application/json','X-CSRF-Token':sessionValue?.csrfToken??''}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});
   if(binary&&response.ok){
    const contentType=response.headers.get('Content-Type');
    if(!contentType||/^(text\/html|application\/json)\b/i.test(contentType))throw new ApiError('原件响应格式无法确认','INVALID_RESPONSE',response.status);
    return {bytes:await response.arrayBuffer(),contentType,disposition:response.headers.get('Content-Disposition')};
   }
   let value;try{value=await response.json()}catch{throw new ApiError('工作台服务响应无效，请重试核对会话','INVALID_RESPONSE',response.status)}
   if(!response.ok||value.error){const error=new ApiError(value.error?.message??'工作台服务请求失败',value.error?.code??'HTTP_ERROR',response.status);error.details=value.error?.details??{};throw error}
   if(value.meta?.mode!=='hosted'||value.meta?.dataOrigin!=='activity_workbench')throw new ApiError('工作台数据模式不匹配，已停止展示','MODE_MISMATCH');return value;
  }catch(error){if(timedOut)throw new ApiError('请求超时；请核对登录结果，未自动重试提交','TIMEOUT');throw error}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel)}
 }
 function remember(value){
  if(!value || !['password','phone_otp'].includes(value.authMethod) || typeof value.authenticated!=='boolean' || typeof value.readOnly!=='boolean')
   throw new ApiError('无法确认登录方式或会话状态','SESSION_SHAPE_MISMATCH');
  if(value.authenticated){
   if(!value.user?.id || typeof value.user.id!=='string' || typeof value.user.username!=='string' ||
      !Array.isArray(value.permissions) || value.permissions.some(p=>typeof p!=='string') ||
      !value.csrfToken || typeof value.csrfToken!=='string')
    throw new ApiError('无法确认当前身份与操作授权','SESSION_SCOPE_MISMATCH');
   if(!Number.isFinite(Date.parse(value.absoluteExpiresAt)) ||
      (value.credentialExpiresAt!=null && !Number.isFinite(Date.parse(value.credentialExpiresAt))))
    throw new ApiError('会话期限无效，请重新登录','SESSION_SHAPE_MISMATCH');
  }
  sessionValue=value;return value;
 }
 const all=createPagedReader(request),read=createQueryReader(request,all);
 return Object.freeze({kind:'hosted',request,all,read,
  async health(){return (await request('/health',{account:true})).data},
  async material(id){return request('/materials/'+encodeURIComponent(id)+'/download',{binary:true})},
  async session({signal}={}){return remember((await request('/session',{account:true,signal})).data)},
  async login(username,password,{signal}={}){return remember((await request('/login',{account:true,method:'POST',body:{username,password},signal})).data)},
  async anonymousLogin({signal}={}){return remember((await request('/auth/anonymous',{account:true,method:'POST',body:{},signal})).data)},
  async requestCode(phone,{signal}={}){return (await request('/auth/otp/request',{account:true,method:'POST',body:{phone},signal})).data},
  async verifyCode(challengeId,code,{signal}={}){return remember((await request('/auth/otp/verify',{account:true,method:'POST',body:{challengeId,code},signal})).data)},
  async logout({signal}={}){const env=await request('/logout',{account:true,method:'POST',body:{},signal});if(env.data.loggedOut!==true)throw new ApiError('尚不能确认退出结果','LOGOUT_UNKNOWN');sessionValue=null;return env.data},
  clear(){sessionValue=null},
  async snapshot(filter,activityId,signal){
   // Same A filters, server ACL + full-scope read snapshot; no client row fallback.
   const params=new URLSearchParams({...queryFilters('product',filter,{linkedOnly:!activityId}),
    ...(activityId?{kind:'activity_products',activityId}:{kind:'product_activity',exportScope:'confirmed_linked'})});
   return request('/exports/snapshot?'+params,{signal});
  }
 })
}
export const hostedAdapter=createHostedAdapter();
