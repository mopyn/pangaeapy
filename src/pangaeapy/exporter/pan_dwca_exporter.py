import os
import re
from collections import OrderedDict

import lxml.etree
import lxml.etree as et
from pangaeapy.exporter.pan_exporter import PanExporter
from zipfile import ZipFile
from io import BytesIO

class PanDarwinCoreAchiveExporter(PanExporter):

    def __init__(self, *args, **kwargs):
        super(PanDarwinCoreAchiveExporter, self).__init__(*args, **kwargs)

        self.dwcnames = {'Event': 'EventID', 'Latitude': 'decimalLatitude', 'Longitude': 'decimalLongitude',
                    'Date/Time': 'eventDate', 'Elevation': 'minimumElevationInMeters'}
        self.dwcfields = ['id', 'modified', 'institutionCode', 'CollectionCode', 'datasetID', 'basisOfRecord', 'catalogNumber',
                     'recordedBy', 'eventDate', 'scientificName', 'kingdom', 'geodeticDatum', 'decimalLatitude',
                     'decimalLongitude', 'minimumElevationInMeters', 'organismQuantity', 'organismQuantityType']

        self.chronostrat_params = [21496, 21497, 21498, 20544, 21197]
        # XYZ zone
        self.biostrat_params = [4491, 15398, 15501, 20543, 21181, 85701, 51065, 57117, 57648, 86368, 89835, 121195,
                           128347, 135504, 170899, 185728]
        # comment
        self.agecomment_params = [643]
        # absolute dated ages
        self.absstrat_params = [2205, 5506, 6167, 6168, 6169, 6170, 70169, 102659, 130805, 145907]
        self.taxonomic_coverage = []

    def check_unit(self, unitexpr):
        unitre = '^([#%])(?:\/((?:[0-9]+\s)?(?:[kdcm]?m{1,2}\*{2}[23]|m?l|k?g)))?(?:\/(d|m|y|a|ka|day|week|month|year){1})?$'
        dimension = ''
        istaxonrelated = False
        if unitexpr:
            if isinstance(unitexpr, str):
                try:
                    umatch = re.search(unitre, unitexpr)
                    if umatch:
                        uparts = filter(None, list(umatch.groups()))
                        if umatch[1] is not None:
                            istaxonrelated = True
                            if umatch[1] == '#':
                                dimension = 'individuals'
                            else:
                                dimension = 'percentage'
                            if umatch[2] is not None:
                                if 'l' in umatch[2] or '**3' in umatch[2]:
                                    dimension += ' per volume'
                                elif 'g' in umatch[2]:
                                    dimension += ' per mass'
                                else:
                                    dimension += ' per area'
                            if umatch[3] is not None:
                                if umatch[2] is not None:
                                    dimension += ' and time'
                                else:
                                    dimension += ' per time'
                except Exception as e:
                    self.logging.append({'WARNING': 'Unit check failed: '+str(e)})
        return istaxonrelated, dimension

    def get_taxon_columns(self):
        taxoncolumns = OrderedDict()
        for pkey, param in self.pandataset.params.items():
            # full match of taxon name with parameter only
            # TODO: extend to some adjectives e.g. juvenile, adult etc..
            try:
                for term in param.terms:
                    # add: #/m3 etc, %/m3 etc
                    is_valid_unit,  dimension = self.check_unit(param.unit)
                    if param.name == term.get('name') and is_valid_unit:
                        if term.get('classification'):
                            if 'Biological Classification' in term.get('classification'):
                                kingdom = list({'Animalia', 'Archaea', 'Bacteria', 'Chromista', 'Fungi', 'Plantae', 'Protozoa',
                                                'Viruses'} & set(term.get('classification')))[0]
                                if kingdom not in self.taxonomic_coverage:
                                    self.taxonomic_coverage.append(kingdom)
                                taxoncolumns[pkey] = {'taxon': param.name, 'series': param.dataseries, 'author': param.PI,
                                                      'kingdom': kingdom, 'colno': param.colno, 'unit': param.unit,'dimension': dimension}
                    break
            except Exception as e:
                self.logging.append({'WARNING': 'Failed to identify taxonomic information in parameter: '+str(pkey)})
        if len(taxoncolumns) <= 0:
            self.logging.append({'WARNING': 'Could not identify taxonomic information in this dataset'})
        return taxoncolumns

    def get_context_info(self):
        geocontext_params = self.chronostrat_params + self.biostrat_params + self.agecomment_params + self.absstrat_params
       # print(geocontext_params)
        basisofrecord = 'HumanObservation'
        geologicalcontextid = None
        try:
            for param in self.pandataset.params.values():
                if param.id:
                    if int(param.id) in geocontext_params:
                        basisofrecord = 'FossilSpecimen'
                        geologicalcontextid = 'http://purl.obolibrary.org/obo/ENVO_00002164'
                    if int(param.id) in [1]:
                        basisofrecord = 'FossilSpecimen'
                        geologicalcontextid = 'http://purl.obolibrary.org/obo/ENVO_00002007'
        except Exception as e:
            self.logging.append({'INFO': 'Failed to identify basisOfRecord based on used geocodes, keep HumanObservation by default'})

        return basisofrecord, geologicalcontextid

    def get_dwca_data(self, taxoncolumns):
        dwcdata = None
        basisofrecord, geologicalcontextid = self.get_context_info()
        selectedcolumns = []
        geocolumns = self.pandataset.defaultparams
        if len(taxoncolumns) > 0:
            selectedcolumns.extend(geocolumns)
            selectedcolumns.extend(taxoncolumns.keys())
            taxonframe = self.pandataset.data[selectedcolumns]
            taxonframe = taxonframe.reset_index()
            geocolumns.append('index')
            taxonframe = taxonframe.melt(id_vars=geocolumns, value_vars=list(taxoncolumns.keys()), var_name='Colname',
                                         value_name='organismQuantity')
            taxonframe['index'] += 1
            taxonframe['id'] = taxonframe['index'].astype(str) + '_' + taxonframe['Colname'].apply(
                lambda x: taxoncolumns.get(x).get('colno')).astype(str)
            taxonframe['modified'] = self.pandataset.lastupdate
            taxonframe['institutionCode'] = 'Pangaea'
            doimatch = re.search('(10\.1594/PANGAEA\.[0-9]+)', self.pandataset.doi)
            taxonframe['CollectionCode'] = 'doi:' + str(doimatch[1])
            taxonframe['datasetID'] = self.pandataset.doi
            taxonframe['basisOfRecord'] = basisofrecord
            taxonframe['catalogNumber'] = taxonframe['Colname'].apply(
                lambda x: taxoncolumns.get(x).get('series')).astype(str) + '_' + taxonframe['index'].astype(str)
            taxonframe['recordedBy'] = taxonframe['Colname'].apply(lambda x: taxoncolumns.get(x).get('author'))
            taxonframe['scientificName'] = taxonframe['Colname'].apply(lambda x: taxoncolumns.get(x).get('taxon'))
            taxonframe['geodeticDatum'] = 'WGS84'
            taxonframe['kingdom'] = taxonframe['Colname'].apply(lambda x: taxoncolumns.get(x).get('kingdom'))
            taxonframe['organismQuantityType'] = 'individuals (' + taxonframe['Colname'].apply(
                lambda x: taxoncolumns.get(x).get('unit')).astype(str) + ')'
            dwcfields= self.dwcfields
            if geologicalcontextid:
                taxonframe['geologicalContextID'] = geologicalcontextid
                dwcfields.append('geologicalContextID')
            taxonframe.rename(columns=self.dwcnames, inplace=True)
            taxonframe = taxonframe[dwcfields]
            dwcdata = taxonframe.to_csv(index=False,sep='|',line_terminator='\n',date_format ='%Y-%m-%dT%H:%M:%S', encoding='utf-8')
            return dwcdata
        else:
            self.logging.append({'ERROR': 'No taxonomic information identified in dataset, skipping DwC-A ASCII table generation'})
            #print('could not identify taxon counts in this data set')
            return False

    def get_meta_xml(self):
        xml='<archive xmlns="http://rs.tdwg.org/dwc/text/" metadata="eml.xml">' \
            '<core encoding="UTF-8" fieldsTerminatedBy="|" linesTerminatedBy="\n" ' \
            'fieldsEnclosedBy="" ignoreHeaderLines="1" rowType="http://rs.tdwg.org/dwc/terms/Occurrence">' \
            '<files><location>96900_data.tab</location></files>' \
            '<id index="0"/>'
        index=1
        try:
            for col in self.dwcfields:
                if col != 'id':
                    if str(col) in ['modified']:
                        xml += '<field index="'+str(index)+'" term="http://purl.org/dc/terms/' + str(col) + '" />'
                    else:
                        xml += '<field index="'+str(index)+'" term="http://rs.tdwg.org/dwc/terms/'+str(col)+'" />'
                    index+=1
        except Exception as e:
            self.logging.append({'ERROR': 'Failed to create DwC-A metadata file'})
            xml=None
        if xml:
            xml += '</core></archive>'
            return xml
        else:
            return False


    def get_eml_xml(self):
        ret = False
        if self.pandataset.metaxml:
            try:
                eml_xslt = os.path.join(os.path.dirname(__file__), 'xslt', 'panmd2eml.xslt')
                xslt = et.parse(eml_xslt)
                panxml = et.fromstring(self.pandataset.metaxml.encode())
                transform = et.XSLT(xslt)
                emlxml = transform(panxml)
                coverage = emlxml.find("dataset/coverage")
                if coverage is not None and self.taxonomic_coverage:
                    taxcovelem = lxml.etree.Element('taxonomicCoverage')
                    coverage.append(taxcovelem)
                    for taxcov in self.taxonomic_coverage:
                        taxcovelem.append(lxml.etree.XML('<taxonomicClassification>'
                                                                    '<taxonRankName>kingdom</taxonRankName>'
                                                                    '<taxonRankValue>'+str(taxcov)+'</taxonRankValue>'
                                                                    '</taxonomicClassification>'))
                #coverage.append(lxml.Element('taxonomicCoverage'))
                ret= et.tostring(emlxml, pretty_print=True)
            except Exception as e:
                self.logging.append({'ERROR': 'Failed to perform XSLT transformation to EML: '+str(e)})
        else:
            self.logging.append({'ERROR': 'Failed to perform XSLT transformation to EML, missing panmd XML'})
        return ret

    def verify(self):
        ret = False
        if self.pandataset.id:
            try:
                datacolumns = self.get_taxon_columns()
                if len(datacolumns) > 0:
                    ret = True
            except Exception as e:
                self.logging.append({'ERROR':'DwC-A verification failed: '+str(e)})

        return ret

    def create(self):
        in_memory_zip = False
        if self.pandataset.id:
            try:
                datacolumns = self.get_taxon_columns()
                data = self.get_dwca_data(datacolumns)
                meta = self.get_meta_xml()
                eml = self.get_eml_xml()
                if not any('ERROR' in lg for lg in self.logging):
                    in_memory_zip = BytesIO()
                    zip_file = ZipFile(in_memory_zip, 'w')
                    zip_file.writestr('meta.xml', meta)
                    zip_file.writestr('eml.xml', eml)
                    zip_file.writestr(self.pandataset.id+'_data.tab', data)
                    zip_file.close()
                    in_memory_zip.seek(0)
                    self.file = in_memory_zip
                else:
                    self.logging.append({'ERROR': 'DwC-A Zip file creation failed due to previous errors '})
            except Exception as e:
                self.logging.append({'ERROR':'DwC-A Zip file creation failed: '+str(e)})
        else:
            self.logging.append({'ERROR': 'Not PanDataSet object available to perform the DwC-A export'})

        return in_memory_zip

    def save(self):
        if isinstance(self.file, BytesIO):
            try:
                with open(os.path.join(self.filelocation,str('dwca_pangaea_'+str(self.pandataset.id)+'.zip')),'wb') as f:
                    #print(f.name)
                    f.write(self.file.getbuffer())
                    f.close()
                    return True
            except Exception as e:
                self.logging.append({'ERROR': 'Could not save, DwC-A Zip: '+str(e)})
        else:
            self.logging.append({'ERROR':'Could not save, DwC-A Zip file is not a BytesIO'})
            return False