from mapbox import Geocoder
tok = 'pk.eyJ1IjoiZ2VvcmdlcG9wMTIzIiwiYSI6ImNsM2VzaDE2cTAybDIzam1vbjlodTlqdWMifQ.mMpWkaLQcGvSLDrqmBON8Q'

geocoder = Geocoder(access_token=tok)

def get_coords(x):

      yy='Москва и Московская Область, '
      yy+= ',Округ '+x['okrug']
      yy+= ',район '+x['district']
      yy+=' '+'г.'+x['address']
      response = geocoder.forward(yy)
      if response.status_code == 200:
          coords = response.json()['features']
          coords=[i['center'] for i in coords  ][0]
          return [coords[1], coords[0]]
      else:
          return None
