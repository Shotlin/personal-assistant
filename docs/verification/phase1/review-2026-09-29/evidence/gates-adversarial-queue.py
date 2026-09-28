import asyncio,json
from assistant.runtime.desktop_queue import DesktopQueue
from assistant.runtime.session import DesktopLeaseBusy
async def main():
 out={}
 q=DesktopQueue(); h=await q.acquire('same')
 try: await q.acquire('same',timeout=.001)
 except DesktopLeaseBusy:pass
 out['duplicate_timeout']={'owner':q.current_owner,'original_handle_usable':q.check_usable(h),'log':q.ownership_log()}
 q=DesktopQueue();h=await q.acquire('same');t=asyncio.create_task(q.acquire('same'));await asyncio.sleep(0);t.cancel()
 try:await t
 except asyncio.CancelledError:pass
 out['duplicate_cancel']={'owner':q.current_owner,'original_handle_usable':q.check_usable(h),'log':q.ownership_log()}
 q=DesktopQueue();h=await q.acquire('A');b=asyncio.create_task(q.acquire('B'));await asyncio.sleep(0);await q.release('A',fence=h.fence);c=await q.acquire('C');before=q.check_usable(c);bh=await b
 out['handoff_barge']={'C_returned_usable':before,'B_returned_usable':q.check_usable(bh),'C_now_usable':q.check_usable(c),'owner':q.current_owner,'log':q.ownership_log()}
 print(json.dumps(out,indent=2))
asyncio.run(main())
