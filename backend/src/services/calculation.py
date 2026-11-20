from collections import defaultdict
from src.services.timecalc import business_minutes
from src.services.validation import validate_source,validate_policy
def calculate(sources,policy):
 validate_policy(policy);pivot=defaultdict(lambda:{'count':0,'minutes':0,'overdue':0});rows=[]
 for s in sources:
  validate_source(s)
  if s['priority'] not in policy['sla_minutes']:raise ValueError('优先级无SLA口径：'+s['priority'])
  mins=business_minutes(s['opened'],s['closed'],policy);limit=policy['sla_minutes'][s['priority']];k=(s['period'],s['department'],s['priority']);p=pivot[k];p['count']+=1;p['minutes']+=mins;p['overdue']+=int(mins>limit)
 for (period,dept,priority),p in sorted(pivot.items()):rows.append({'月份':period,'小组':dept,'优先级':priority,'样本数':p['count'],'业务分钟合计':p['minutes'],'平均业务分钟':p['minutes']/p['count'],'超时数':p['overdue'],'超时率':p['overdue']/p['count']})
 return {'summary':{'样本数':len(sources),'业务分钟合计':sum(p['minutes'] for p in pivot.values()),'超时数':sum(p['overdue'] for p in pivot.values()),'时区':policy['timezone']},'rows':rows,'method':'ISO时区转换至业务日历时区后，逐日求开始/结束区间与不重叠工作时窗交集；排除非工作日、显式假日与午休。恰等于SLA阈值不算超时。','warnings':['统计合成已结束样本；不包含未关闭请求，也不承诺实时事件提醒。']}
