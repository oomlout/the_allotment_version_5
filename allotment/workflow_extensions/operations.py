"""Optional reusable Windows observation and data primitives."""
import re
try:
    from ..roboclick.workflow import Operations,NotReady,READ_ONLY
except ImportError:
    from roboclick_workflow import Operations,NotReady,READ_ONLY

# These extensions observe state only; they never inject input.
READ_ONLY.update({'desktop_capture_accessibility','data_elements_check'})

class ExtendedOperations(Operations):
    def capture_accessibility(self,**options):
        try:from .accessibility import capture
        except ImportError:from roboclick_accessibility import capture
        return capture(self.desktop,**options)

    def elements_check(self,snapshot,match,min_count=0,max_count=None,on_mismatch='pending',message='Waiting for the required controls'):
        if on_mismatch not in {'pending','error'}:raise ValueError('Invalid element check mode.')
        if not isinstance(snapshot,dict) or not isinstance(snapshot.get('elements'),list):raise ValueError('Missing accessibility snapshot.')
        if not match or set(match)-{'control_type','name','name_regex','enabled','automation_id'}:raise ValueError('Invalid accessibility match.')
        def matches(item):
            return all((re.search(value,item.get('name','')) is not None if key=='name_regex' else item.get(key)==value)
                       for key,value in match.items())
        count=sum(matches(item) for item in snapshot['elements'])
        if count<int(min_count) or (max_count is not None and count>int(max_count)):
            raise (NotReady if on_mismatch=='pending' else ValueError)(message)
        return {'capture_id':snapshot['capture_id'],'count':count}

