/* Decode complete JPEG bodies immediately; native MJPEG <img> can lag a frame. */
(() => {
 class MultipartFrames {
  constructor(onFrame){this.onFrame=onFrame;this.buffer=new Uint8Array();this.header=null;}
  feed(chunk){
   if(this.buffer.length+chunk.length>8*1024*1024)throw new Error('Browser frame buffer exceeded its limit');
   const combined=new Uint8Array(this.buffer.length+chunk.length);combined.set(this.buffer);combined.set(chunk,this.buffer.length);this.buffer=combined;
   while(true){
    if(!this.header){
     let end=-1;for(let i=0;i+3<this.buffer.length&&i<8192;i++)if(this.buffer[i]===13&&this.buffer[i+1]===10&&this.buffer[i+2]===13&&this.buffer[i+3]===10){end=i;break;}
     if(end<0){if(this.buffer.length>8192)throw new Error('Invalid browser frame header');return;}
     const text=new TextDecoder().decode(this.buffer.subarray(0,end)),length=Number(text.match(/^Content-Length:[ \t]*(\d+)[ \t]*$/im)?.[1]);
     if(!Number.isInteger(length)||length<1||length>4*1024*1024)throw new Error('Invalid browser frame length');
     this.header={length,sequence:text.match(/X-Frame-Sequence:\s*(\d+)/i)?.[1]??null,inputId:Number(text.match(/X-Input-Command:\s*(\d+)/i)?.[1]||0)};
     this.buffer=this.buffer.subarray(end+4);
    }
    if(this.buffer.length<this.header.length)return;
    const frame={...this.header,bytes:this.buffer.subarray(0,this.header.length),receivedAt:performance.now()};
    this.buffer=this.buffer.subarray(this.header.length);this.header=null;this.onFrame(frame);
   }
  }
 }
 class FrameViewer {
  constructor(canvas,onFrame){this.canvas=canvas;this.onFrame=onFrame;this.pending=null;this.decoding=false;this.running=false;this.lastSequence=null;}
  async start(url){
   this.stop();this.running=true;const controller=this.controller=new AbortController();
   const response=await fetch(url,{cache:'no-store',signal:controller.signal});
   if(!response.ok||!response.headers.get('content-type')?.includes('multipart/x-mixed-replace'))throw new Error('Browser frames unavailable');
   const reader=response.body.getReader(),parser=new MultipartFrames(frame=>{if(this.running&&this.controller===controller){this.pending=frame;this.render();}});
   try{while(this.running&&this.controller===controller){const {value,done}=await reader.read();if(done)break;parser.feed(value);}}
   finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  async render(){
   if(this.decoding)return;this.decoding=true;
   try{
    while(this.pending&&this.running){
     const frame=this.pending,controller=this.controller;this.pending=null;
     if(frame.sequence!==null&&frame.sequence===this.lastSequence)continue;
     const blob=new Blob([frame.bytes],{type:'image/jpeg'}),bitmap=await createImageBitmap(blob);
     try{
      if(!this.running||this.controller!==controller)continue;
      if(this.canvas.width!==bitmap.width)this.canvas.width=bitmap.width;if(this.canvas.height!==bitmap.height)this.canvas.height=bitmap.height;
      this.canvas.getContext('2d',{alpha:false}).drawImage(bitmap,0,0);this.canvas.hidden=false;this.lastSequence=frame.sequence;
      this.onFrame?.({...frame,blob,paintedAt:performance.now()});
     }finally{bitmap.close();}
    }
   }catch{this.stop();this.canvas.hidden=true;this.onError?.();}
   finally{this.decoding=false;}
  }
  stop(){this.running=false;this.pending=null;this.controller?.abort();}
 }
 globalThis.BrowserFrameView={MultipartFrames,FrameViewer};
})();
