from fastapi import APIRouter,Depends
from src.config.database import database
from src.controllers.auth import current,editor
from src.services import segment as service
router=APIRouter()
@router.get('/segments')
def segments(db=Depends(database),u=Depends(current)):
 return [x for x in (service.view(db,r.run_id) for r in service.all_rows(db)) if x]
@router.get('/runs/{id}/segment')
def segment(id:int,db=Depends(database),u=Depends(current)):
 return service.view(db,id) or {'run_id':id,'status':'idle'}
@router.post('/runs/{id}/segment')
def search(id:int,db=Depends(database),u=Depends(editor)):
 service.request(db,u,id)
 return service.view(db,id)
