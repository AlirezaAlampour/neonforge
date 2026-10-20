"""Monotone Hermite world paths: preserve waypoints and stationary intervals."""


def sample(path,t,fallback):
    if not path:return tuple(fallback)
    if t<=path[0]['time']:return tuple(path[0]['position'][k] for k in 'xyz')
    if t>=path[-1]['time']:return tuple(path[-1]['position'][k] for k in 'xyz')
    i=next(i for i in range(len(path)-1) if path[i]['time']<=t<=path[i+1]['time'])
    times=[p['time'] for p in path];span=times[i+1]-times[i];u=(t-times[i])/span
    result=[]
    for axis in 'xyz':
        values=[p['position'][axis] for p in path]
        slopes=[(b-a)/(tb-ta) for a,b,ta,tb in zip(values,values[1:],times,times[1:])]
        def tangent(j):
            if j==0:return slopes[0]
            if j==len(path)-1:return slopes[-1]
            left,right=slopes[j-1:j+1]
            if left*right<=0:return 0
            a,b=times[j]-times[j-1],times[j+1]-times[j]
            w1,w2=2*b+a,b+2*a
            return (w1+w2)/(w1/left+w2/right)
        a,b=values[i:i+2]
        value=(2*u**3-3*u*u+1)*a+(u**3-2*u*u+u)*span*tangent(i)+(-2*u**3+3*u*u)*b+(u**3-u*u)*span*tangent(i+1)
        # Preserve stationary axes exactly and bound floating point roundoff.
        result.append(a if a==b else max(min(a,b),min(max(a,b),value)))
    return tuple(result)
